import logging
from ERP.utils.thread_local import get_current_request

class ClientIPFilter(logging.Filter):
    def filter(self, record):
        request = get_current_request()
        if request:
            ip = request.META.get('REMOTE_ADDR', '')
            record.client_ip = ip
        else:
            record.client_ip = 'Unknown'
        return True