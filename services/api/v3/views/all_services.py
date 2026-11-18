from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from services.models import GeneralService
from services.api.v3.serializers import GeneralServiceListSerializer


class AllServicesView(APIView):
    @extend_schema(
        tags=["Service & Product Management V3"],
        summary="List all active general services",
        description="Returns all active general services for service selection screens.",
        responses={
            200: OpenApiResponse(description="Array response of active general services."),
        },
        examples=[
            OpenApiExample(
                "Success response",
                value=[{"id": 1, "name": "Tailoring", "description": "Tailoring service", "icon": "https://..."}],
                response_only=True,
                status_codes=["200"],
            ),
        ],
    )
    def get(self, request):
        services = GeneralService.objects.filter(is_active=True)
        serializer = GeneralServiceListSerializer(services, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
