from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, status
from fastapi.responses import JSONResponse
from typing import Optional
import logging
from datetime import datetime

from app.models.schemas import (
    ContentApprovalRequest,
    ApprovalResponse,
    ErrorResponse,
    create_error_response
)
from app.models.database import DatabaseManager, DatabaseUtils
from app.services.linkedin_service import LinkedInService
from app.services.telegram_service import TelegramService
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

# Initialize services
linkedin_service = LinkedInService()
telegram_service = TelegramService()
db_manager = DatabaseManager(settings.DATABASE_URL)


@router.post(
    "/approve",
    response_model=ApprovalResponse,
    status_code=status.HTTP_200_OK,
    summary="Approve or Reject Content",
    description="Human-in-the-loop approval workflow for generated content",
    responses={
        200: {"description": "Approval processed successfully"},
        400: {"description": "Invalid approval request"},
        404: {"description": "Content not found"},
        500: {"description": "Internal server error"}
    }
)
async def approve_content(
    approval_request: ContentApprovalRequest,
    background_tasks: BackgroundTasks,
    user_id: str = "default_user"
):
    """
    Approve or reject generated content.
    
    This endpoint:
    - Processes human approval/rejection from Telegram
    - Applies edits if provided
    - Posts to LinkedIn if approved
    - Updates content status accordingly
    """
    try:
        logger.info(f"Processing approval for content: {approval_request.content_id}")
        
        with db_manager.get_session() as session:
            from app.models.database import Content, ContentStatusDB, ApprovalWorkflow, LinkedInPost
            
            # Get content from database
            content = session.query(Content).filter(
                Content.content_id == approval_request.content_id,
                Content.user_id == user_id
            ).first()
            
            if not content:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Content with ID {approval_request.content_id} not found"
                )
            
            # Get approval workflow record
            workflow = session.query(ApprovalWorkflow).filter(
                ApprovalWorkflow.content_id == approval_request.content_id
            ).first()
            
            if approval_request.approved:
                # Handle approval
                if approval_request.edits:
                    # Apply user edits
                    content.content_text = approval_request.edits
                    content.status = ContentStatusDB.EDITED_APPROVED
                    logger.info(f"Content edited and approved: {approval_request.content_id}")
                else:
                    content.status = ContentStatusDB.APPROVED
                    logger.info(f"Content approved without edits: {approval_request.content_id}")
                
                # Update image if selected
                if approval_request.image_choice:
                    content.image_url = approval_request.image_choice
                
                content.updated_at = datetime.now()
                
                # Update approval workflow
                if workflow:
                    workflow.approved_at = datetime.now()
                    workflow.approved_by = user_id
                    workflow.edited_content = approval_request.edits
                    workflow.is_completed = True
                
                # Post to LinkedIn in background
                background_tasks.add_task(
                    post_to_linkedin_background,
                    content_id=approval_request.content_id,
                    user_id=user_id,
                    content_text=content.content_text,
                    image_url=content.image_url
                )
                
                message = "Content approved and queued for LinkedIn posting"
                status_msg = "approved"
                
            else:
                # Handle rejection
                content.status = ContentStatusDB.REJECTED
                content.updated_at = datetime.now()
                
                # Update approval workflow
                if workflow:
                    workflow.rejection_reason = "Rejected by user"
                    workflow.is_completed = True
                
                message = "Content rejected"
                status_msg = "rejected"
                logger.info(f"Content rejected: {approval_request.content_id}")
            
            session.commit()
            
            # Send confirmation to Telegram
            background_tasks.add_task(
                telegram_service.send_approval_confirmation,
                user_id=user_id,
                content_id=approval_request.content_id,
                approved=approval_request.approved,
                message=message
            )
            
            response = ApprovalResponse(
                content_id=approval_request.content_id,
                status=content.status.value,
                message=message
            )
            
            return response
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing approval: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Approval processing failed: {str(e)}"
        )


@router.get(
    "/workflow/{content_id}",
    summary="Get Approval Workflow Status",
    description="Retrieve the current status of approval workflow for specific content",
    responses={
        200: {"description": "Workflow status retrieved successfully"},
        404: {"description": "Workflow not found"},
        500: {"description": "Internal server error"}
    }
)
async def get_approval_workflow(content_id: str, user_id: str = "default_user"):
    """
    Get detailed approval workflow status for specific content.
    
    Returns workflow timeline, current status, and any actions taken.
    """
    try:
        logger.info(f"Retrieving approval workflow for content: {content_id}")
        
        with db_manager.get_session() as session:
            from app.models.database import Content, ApprovalWorkflow
            
            # Verify content exists and belongs to user
            content = session.query(Content).filter(
                Content.content_id == content_id,
                Content.user_id == user_id
            ).first()
            
            if not content:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Content with ID {content_id} not found"
                )
            
            # Get workflow details
            workflow = session.query(ApprovalWorkflow).filter(
                ApprovalWorkflow.content_id == content_id
            ).first()
            
            if not workflow:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Approval workflow not found for content {content_id}"
                )
            
            # Prepare response
            workflow_data = {
                "content_id": workflow.content_id,
                "sent_for_approval_at": workflow.sent_for_approval_at.isoformat() if workflow.sent_for_approval_at else None,
                "approved_at": workflow.approved_at.isoformat() if workflow.approved_at else None,
                "approved_by": workflow.approved_by,
                "rejection_reason": workflow.rejection_reason,
                "is_completed": workflow.is_completed,
                "telegram_message_id": workflow.telegram_message_id,
                "created_at": workflow.created_at.isoformat(),
                "updated_at": workflow.updated_at.isoformat() if workflow.updated_at else None
            }
            
            return {
                "workflow": workflow_data,
                "content_status": content.status.value,
                "current_stage": "awaiting_approval" if not workflow.is_completed else "completed"
            }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving workflow: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve approval workflow: {str(e)}"
        )


@router.post(
    "/{content_id}/send-reminder",
    status_code=status.HTTP_200_OK,
    summary="Send Approval Reminder",
    description="Send reminder to Telegram for pending approval",
    responses={
        200: {"description": "Reminder sent successfully"},
        404: {"description": "Content not found"},
        400: {"description": "Content not pending approval"},
        500: {"description": "Internal server error"}
    }
)
async def send_approval_reminder(
    content_id: str,
    background_tasks: BackgroundTasks,
    user_id: str = "default_user"
):
    """
    Send approval reminder for content pending approval.
    
    Useful when user hasn't responded to initial approval request.
    """
    try:
        logger.info(f"Sending approval reminder for content: {content_id}")
        
        with db_manager.get_session() as session:
            from app.models.database import Content, ContentStatusDB
            
            content = session.query(Content).filter(
                Content.content_id == content_id,
                Content.user_id == user_id
            ).first()
            
            if not content:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Content with ID {content_id} not found"
                )
            
            # Check if content is pending approval
            if content.status != ContentStatusDB.PENDING_APPROVAL:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Cannot send reminder for content with status: {content.status.value}"
                )
            
            # Send reminder in background
            background_tasks.add_task(
                telegram_service.send_approval_reminder,
                user_id=user_id,
                content_id=content_id,
                content_text=content.content_text
            )
            
            return {
                "message": "Approval reminder sent successfully",
                "content_id": content_id,
                "reminder_sent_at": datetime.now().isoformat()
            }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error sending approval reminder: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to send approval reminder: {str(e)}"
        )


@router.get(
    "/pending/count",
    summary="Get Pending Approval Count",
    description="Get the number of content items pending approval for the user",
    responses={
        200: {"description": "Count retrieved successfully"},
        500: {"description": "Internal server error"}
    }
)
async def get_pending_approval_count(user_id: str = "default_user"):
    """
    Get count of content items awaiting user approval.
    
    Useful for showing notifications or dashboard metrics.
    """
    try:
        logger.info(f"Getting pending approval count for user: {user_id}")
        
        with db_manager.get_session() as session:
            from app.models.database import Content, ContentStatusDB
            
            count = session.query(Content).filter(
                Content.user_id == user_id,
                Content.status == ContentStatusDB.PENDING_APPROVAL
            ).count()
            
            return {
                "user_id": user_id,
                "pending_approval_count": count,
                "retrieved_at": datetime.now().isoformat()
            }
            
    except Exception as e:
        logger.error(f"Error getting pending count: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get pending approval count: {str(e)}"
        )


@router.post(
    "/{content_id}/cancel-approval",
    status_code=status.HTTP_200_OK,
    summary="Cancel Approval Request",
    description="Cancel pending approval request and mark content as draft",
    responses={
        200: {"description": "Approval cancelled successfully"},
        404: {"description": "Content not found"},
        400: {"description": "Content not pending approval"},
        500: {"description": "Internal server error"}
    }
)
async def cancel_approval(
    content_id: str,
    user_id: str = "default_user"
):
    """
    Cancel pending approval request.
    
    Returns content to draft status and removes from approval queue.
    """
    try:
        logger.info(f"Canceling approval for content: {content_id}")
        
        with db_manager.get_session() as session:
            from app.models.database import Content, ContentStatusDB, ApprovalWorkflow
            
            content = session.query(Content).filter(
                Content.content_id == content_id,
                Content.user_id == user_id
            ).first()
            
            if not content:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Content with ID {content_id} not found"
                )
            
            # Check if content is pending approval
            if content.status != ContentStatusDB.PENDING_APPROVAL:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Cannot cancel approval for content with status: {content.status.value}"
                )
            
            # Update content status
            content.status = ContentStatusDB.DRAFT
            content.updated_at = datetime.now()
            
            # Update workflow
            workflow = session.query(ApprovalWorkflow).filter(
                ApprovalWorkflow.content_id == content_id
            ).first()
            
            if workflow:
                workflow.is_completed = True
                workflow.rejection_reason = "Cancelled by user"
            
            session.commit()
            
            # Notify Telegram (optional - could remove approval message)
            
            return {
                "message": "Approval request cancelled successfully",
                "content_id": content_id,
                "new_status": "draft"
            }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error canceling approval: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to cancel approval: {str(e)}"
        )


# Background task functions
async def post_to_linkedin_background(
    content_id: str,
    user_id: str,
    content_text: str,
    image_url: Optional[str] = None
):
    """
    Background task to post approved content to LinkedIn.
    
    This runs asynchronously to avoid blocking the approval response.
    """
    try:
        logger.info(f"Posting to LinkedIn in background: {content_id}")
        
        with db_manager.get_session() as session:
            from app.models.database import Content, ContentStatusDB, LinkedInPost
            
            # Update content status to indicate posting in progress
            content = session.query(Content).filter(
                Content.content_id == content_id,
                Content.user_id == user_id
            ).first()
            
            if not content:
                logger.error(f"Content not found for LinkedIn posting: {content_id}")
                return
            
            try:
                # Post to LinkedIn
                post_result = await linkedin_service.post_content(
                    content=content_text,
                    image_url=image_url
                )
                
                # Update content with LinkedIn post details
                content.status = ContentStatusDB.POSTED
                content.linkedin_post_id = post_result.get("post_id")
                content.linkedin_post_url = post_result.get("post_url")
                content.posted_at = datetime.now()
                content.updated_at = datetime.now()
                
                # Create LinkedIn post record
                linkedin_post = LinkedInPost(
                    content_id=content_id,
                    user_id=user_id,
                    linkedin_post_id=post_result.get("post_id"),
                    post_url=post_result.get("post_url"),
                    post_content=content_text,
                    posted_successfully=True,
                    posted_at=datetime.now()
                )
                session.add(linkedin_post)
                
                session.commit()
                
                # Send success notification
                await telegram_service.send_post_success_notification(
                    user_id=user_id,
                    content_id=content_id,
                    post_url=post_result.get("post_url")
                )
                
                logger.info(f"Successfully posted to LinkedIn: {content_id}")
                
            except Exception as linkedin_error:
                # Handle LinkedIn posting errors
                logger.error(f"LinkedIn posting failed: {linkedin_error}")
                
                content.status = ContentStatusDB.FAILED
                content.updated_at = datetime.now()
                
                # Create failed LinkedIn post record
                linkedin_post = LinkedInPost(
                    content_id=content_id,
                    user_id=user_id,
                    post_content=content_text,
                    posted_successfully=False,
                    error_message=str(linkedin_error),
                    posted_at=datetime.now()
                )
                session.add(linkedin_post)
                
                session.commit()
                
                # Send failure notification
                await telegram_service.send_post_failure_notification(
                    user_id=user_id,
                    content_id=content_id,
                    error_message=str(linkedin_error)
                )
                
    except Exception as e:
        logger.error(f"Background LinkedIn posting failed: {str(e)}", exc_info=True)