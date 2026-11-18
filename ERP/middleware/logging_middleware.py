import time
import json
import logging
import re

from django.conf import settings
from django.utils.deprecation import MiddlewareMixin
from django.http import JsonResponse, QueryDict
from ERP.utils.thread_local import set_current_request, clear_request
from ERP.utils.user_helpers import get_user_info, get_shop_info  

logger = logging.getLogger('api_requests')


class RequestResponseLoggingMiddleware(MiddlewareMixin):
    SENSITIVE_KEYS = ['password', 'new_password', 'old_password', 'secret', 'token']
    REDACTION_TEXT = "********"
    MAX_BODY_BYTES = getattr(settings, 'LOG_REQUEST_BODY_MAX_BYTES', 256 * 1024)  # 256KB
    MAX_FIELD_CHARS = getattr(settings, 'LOG_REQUEST_FIELD_MAX_CHARS', 2000)

    def process_request(self, request):
        set_current_request(request)
        request._start_time = time.time()
        request._body_copy = self._get_request_body_copy(request)

    def process_response(self, request, response):
        self._log_if_needed(request, response=response)
        clear_request()
        
        if response.status_code == 401 and self._is_token_error(response):
            return self._simplify_token_error_response(response)
        return response

    def process_exception(self, request, exception):
        response = self._log_if_needed(request, exception=exception)
        clear_request()
        return response

    def _is_token_error(self, response):
        try:
            response_data = json.loads(response.content.decode('utf-8'))
            
            if 'detail' in response_data:
                detail = response_data['detail'].lower()
                return any(
                    phrase in detail 
                    for phrase in ['token', 'authentication credentials', 'not provided', 'invalid', 'expired', 'password change', 'refresh token invalidated']
                )
            
            if 'dev_message' in response_data:
                dev_msg = response_data['dev_message'].lower()
                return 'token' in dev_msg
            
            return False
        except:
            return False

    def _simplify_token_error_response(self, response):
        try:
            original_data = json.loads(response.content.decode('utf-8'))
            detail_msg = original_data.get("detail", "").lower()
            
            if "password change" in detail_msg or "refresh token invalidated" in detail_msg:
                simplified_response = {
                    "status": "error",
                    "message": "Password changed. Please login again.",
                    "data": None,
                    "dev_message": "Token is invalid or expired"
                }

            else:
                simplified_response = {
                    "status": "error",
                    "message": "Authentication failed",
                    "data": None,
                    "dev_message": "Token is invalid or expired"
                }
            
            response._original_content = original_data
            response.content = json.dumps(simplified_response).encode('utf-8')
            response['Content-Length'] = len(response.content)
            return response
        except Exception:
            return response

    def _log_if_needed(self, request, response=None, exception=None):
        try:
            status_code = getattr(response, 'status_code', 500)
            if status_code < 400 and not exception:
                return

            duration = (time.time() - getattr(request, '_start_time', time.time())) * 1000

            # Get redacted request body
            request_data = self._get_redacted_request_body(request)
            
            # Get redacted response data
            response_data = self._get_redacted_response_data(response)

            user = getattr(request, 'user', None)
            user_name, user_id = get_user_info(user)
            shop_name, shop_id = get_shop_info(request)
            client_ip = self.get_client_ip(request)

            log_message = (
                f"API Exception:\n\n"
                f"User: {user_name} (ID: {user_id})\n"
                f"Shop: {shop_name} (ID: {shop_id})\n"
                f"Client IP: {client_ip}\n"
                f"Method: {request.method}\n"
                f"Path: {request.get_full_path()}\n"
                f"Status: {status_code}\n\n"
                f"Request Body:\n{request_data}\n\n"
                f"Response:\n{response_data}\n\n"
                f"Time Taken: {duration:.2f}ms"
            )

            if status_code >= 500:
                logger.error(log_message)
            else:
                logger.warning(log_message)

        except Exception as e:
            logger.exception("Failed to log API exception: %s", str(e))

    def _get_redacted_request_body(self, request):
        """Returns request body with sensitive information redacted"""
        try:
            if not hasattr(request, '_body_copy') or request._body_copy is None:
                return "{}"

            body_copy = request._body_copy.copy() if isinstance(request._body_copy, dict) else {}
            self._redact_sensitive_data(body_copy)

            # Avoid blank sections in logs/Discord
            return json.dumps(body_copy, indent=2) if body_copy is not None else "{}"
        except Exception:
            return "Unable to parse request body"

    def _safe_decode(self, data: bytes) -> str:
        return data.decode('utf-8', errors='replace') if data else ''

    def _parse_urlencoded_body(self, request) -> dict:
        """Parse x-www-form-urlencoded body for non-POST methods (PATCH/PUT)."""
        try:
            content_length = int(request.META.get('CONTENT_LENGTH') or 0)
            if content_length and content_length > self.MAX_BODY_BYTES:
                return {
                    "_body_omitted": "urlencoded too large",
                    "content_length": content_length,
                }
            raw = request.body or b''
            if not raw:
                return {}

            qd = QueryDict(self._safe_decode(raw), encoding='utf-8')
            data_dict = {}
            for key in qd.keys():
                values = qd.getlist(key)
                data_dict[key] = values if len(values) > 1 else (values[0] if values else None)
            return data_dict
        except Exception as e:
            return {"_urlencoded_parse_error": str(e)}

    def _parse_multipart_body_fields(self, request) -> dict:
        """Best-effort multipart parsing for PATCH/PUT without touching request.POST.

        Notes:
        - Django doesn't populate request.POST for PATCH/PUT.
        - We avoid parsing full files; we only extract small form fields and file metadata.
        - We guard with a max content-length to avoid excessive memory usage.
        """
        content_type = request.META.get('CONTENT_TYPE', '')
        try:
            content_length = int(request.META.get('CONTENT_LENGTH') or 0)
            if content_length and content_length > self.MAX_BODY_BYTES:
                return {
                    "_body_omitted": "multipart too large",
                    "content_length": content_length,
                }

            boundary_match = re.search(r'boundary=(?P<b>[^;]+)', content_type)
            if not boundary_match:
                return {"_multipart_parse_error": "boundary missing"}

            boundary = boundary_match.group('b').strip().strip('"')
            if not boundary:
                return {"_multipart_parse_error": "boundary empty"}

            raw = request.body or b''
            if not raw:
                return {}

            boundary_bytes = boundary.encode('utf-8')
            delimiter = b'--' + boundary_bytes
            parts = raw.split(delimiter)

            data = {}
            files_meta = {}

            for part in parts:
                part = part.strip(b'\r\n')
                if not part or part == b'--':
                    continue
                if part.endswith(b'--'):
                    part = part[:-2]

                header_blob, sep, value_blob = part.partition(b'\r\n\r\n')
                if not sep:
                    continue

                headers = self._safe_decode(header_blob).split('\r\n')
                disposition = next((h for h in headers if h.lower().startswith('content-disposition:')), '')
                if not disposition:
                    continue

                name_match = re.search(r'name="?([^";]+)"?', disposition)
                if not name_match:
                    continue
                field_name = name_match.group(1)

                filename_match = re.search(r'filename="?([^";]+)"?', disposition)
                if filename_match:
                    meta = {"filename": filename_match.group(1)}
                    ctype = next((h for h in headers if h.lower().startswith('content-type:')), '')
                    if ctype:
                        meta["content_type"] = ctype.split(':', 1)[1].strip()
                    files_meta[field_name] = meta
                    continue

                field_value = value_blob.rstrip(b'\r\n')
                decoded_value = self._safe_decode(field_value)
                if len(decoded_value) > self.MAX_FIELD_CHARS:
                    decoded_value = decoded_value[: self.MAX_FIELD_CHARS] + "…(truncated)"

                if field_name in data:
                    if not isinstance(data[field_name], list):
                        data[field_name] = [data[field_name]]
                    data[field_name].append(decoded_value)
                else:
                    data[field_name] = decoded_value

            if files_meta:
                data['_uploaded_files'] = files_meta

            return data

        except Exception as e:
            return {"_multipart_parse_error": str(e)}

    def _get_redacted_response_data(self, response):
        """Returns response data with sensitive information redacted"""
        if response is None or not hasattr(response, 'content'):
            return "No response content"

        try:
            if hasattr(response, '_original_content'):
                response_data = response._original_content.copy()
            else:
                response_data = json.loads(response.content.decode('utf-8'))
            
            if isinstance(response_data, dict):
                self._redact_sensitive_data(response_data)
                return json.dumps(response_data, indent=2)
            
            return str(response_data)[:2000]
        except json.JSONDecodeError:
            return response.content.decode('utf-8', errors='replace')[:2000]
        except Exception:
            return "Unable to parse response"

    def _redact_sensitive_data(self, data):
        """Recursively redacts sensitive keys in dictionaries"""
        if isinstance(data, dict):
            for key in list(data.keys()):
                if any(sensitive_key in key.lower() for sensitive_key in self.SENSITIVE_KEYS):
                    data[key] = self.REDACTION_TEXT
                elif isinstance(data[key], (dict, list)):
                    self._redact_sensitive_data(data[key])
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, (dict, list)):
                    self._redact_sensitive_data(item)

    def _get_request_body_copy(self, request):
        """
        Extract request body for logging.
        Handles JSON, form-data, and multipart/form-data with file uploads.
        Important: For multipart requests, we must NOT access request.body as it's already
        parsed into request.POST and request.FILES to avoid memory issues.
        """
        try:
            content_type = request.META.get('CONTENT_TYPE', '')
            method = (getattr(request, 'method', '') or '').upper()
            
            # Handle JSON requests
            if 'application/json' in content_type:
                try:
                    body = request.body
                    return json.loads(body.decode('utf-8')) if body else {}
                except Exception as e:
                    return {"error": f"Failed to parse JSON: {str(e)}"}

            # Handle form-urlencoded and multipart/form-data
            elif 'application/x-www-form-urlencoded' in content_type or 'multipart/form-data' in content_type:
                data_dict = {}
                
                # For POST, Django parses form fields into request.POST safely.
                # For PATCH/PUT, Django typically leaves request.POST empty; DRF parses request.data later.
                # So for PATCH/PUT we do a best-effort parse from request.body with strict size limits.
                if method == 'POST':
                    try:
                        if hasattr(request, 'POST') and request.POST:
                            for key in request.POST.keys():
                                values = request.POST.getlist(key)
                                data_dict[key] = values if len(values) > 1 else (values[0] if values else None)
                    except Exception as e:
                        data_dict['_post_parse_error'] = str(e)
                else:
                    if 'application/x-www-form-urlencoded' in content_type:
                        data_dict.update(self._parse_urlencoded_body(request))
                    elif 'multipart/form-data' in content_type:
                        data_dict.update(self._parse_multipart_body_fields(request))
                
                # Capture FILES data (for multipart/form-data) - metadata only
                try:
                    if hasattr(request, 'FILES') and request.FILES:
                        files_info = {}
                        for field_name in request.FILES.keys():
                            uploaded_files = request.FILES.getlist(field_name)
                            # Log file metadata instead of actual content
                            files_info[field_name] = [
                                {
                                    "name": getattr(f, 'name', 'unknown'),
                                    "size": getattr(f, 'size', 0),
                                    "content_type": getattr(f, 'content_type', 'unknown')
                                }
                                for f in uploaded_files
                            ]
                        if files_info:
                            data_dict['_uploaded_files'] = files_info
                except Exception as e:
                    data_dict['_files_parse_error'] = str(e)
                
                return data_dict

            # Fallback for other content types
            else:
                try:
                    body = request.body
                    if body:
                        return {"raw_body": body.decode('utf-8', errors='replace')[:2000]}
                    return {}
                except Exception as e:
                    return {"error": f"Failed to read body: {str(e)}"}
                
        except Exception as e:
            logger.error(f"Failed to parse request body in logging middleware: {str(e)}")
            return {"_logging_error": str(e)}

    def get_client_ip(self, request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0]
        return request.META.get('REMOTE_ADDR')