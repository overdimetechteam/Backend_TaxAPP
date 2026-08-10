from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404

from .models import Document
from .serializers import DocumentSerializer, DocumentUploadSerializer
from apps.clients.models import ClientProfile
from apps.tax_forms.views import _get_submission_for_user


class DocumentListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, submission_id):
        submission = _get_submission_for_user(submission_id, request.user)
        if not submission:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        documents = Document.objects.filter(submission=submission)
        serializer = DocumentSerializer(documents, many=True, context={'request': request})
        return Response(serializer.data)

    def post(self, request, submission_id):
        submission = _get_submission_for_user(submission_id, request.user)
        if not submission:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        if submission.status not in ['draft', 'info_requested']:
            return Response({'error': 'Cannot upload documents in current status.'}, status=status.HTTP_400_BAD_REQUEST)

        serializer = DocumentUploadSerializer(data=request.data)
        if serializer.is_valid():
            file = serializer.validated_data['file']
            doc = Document.objects.create(
                submission=submission,
                uploaded_by=request.user,
                document_type=serializer.validated_data['document_type'],
                section=serializer.validated_data.get('section', 'general'),
                file=file,
                original_filename=file.name,
                file_size=file.size,
                description=serializer.validated_data.get('description', ''),
            )
            return Response(DocumentSerializer(doc, context={'request': request}).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class DocumentDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_document(self, pk, user):
        doc = get_object_or_404(Document, id=pk)
        if not _get_submission_for_user(doc.submission_id, user):
            return None
        return doc

    def get(self, request, pk):
        doc = self.get_document(pk, request.user)
        if not doc:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(DocumentSerializer(doc, context={'request': request}).data)

    def delete(self, request, pk):
        doc = get_object_or_404(Document, id=pk, uploaded_by=request.user)
        if doc.submission.status not in ['draft', 'info_requested']:
            return Response({'error': 'Cannot delete documents in current status.'}, status=status.HTTP_400_BAD_REQUEST)
        doc.file.delete(save=False)
        doc.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class VerifyDocumentView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        if request.user.role != 'consultant':
            return Response({'error': 'Permission denied.'}, status=status.HTTP_403_FORBIDDEN)

        client_ids = ClientProfile.objects.filter(
            assigned_consultant=request.user
        ).values_list('user_id', flat=True)

        doc = get_object_or_404(Document, id=pk, submission__client_id__in=client_ids)
        doc.is_verified = True
        doc.verified_by = request.user
        doc.save()
        return Response({'message': 'Document verified.'})
