import logging
import asyncio
import httpx
from typing import Dict, Optional, Any, List
from datetime import datetime
import base64
import json
from urllib.parse import urlencode

from app.core.config import settings
from app.models.schemas import LinkedInPostRequest, ErrorResponse
from app.models.database import DatabaseManager

logger = logging.getLogger(__name__)


class LinkedInService:
    """
    LinkedIn API service for posting content to LinkedIn.
    
    Handles:
    - LinkedIn OAuth2 authentication
    - Posting text and image content
    - Handling LinkedIn API rate limits and errors
    - Post metrics and tracking
    """
    
    def __init__(self):
        self.client_id = settings.LINKEDIN_CLIENT_ID
        self.client_secret = settings.LINKEDIN_CLIENT_SECRET
        self.access_token = settings.LINKEDIN_ACCESS_TOKEN
        self.redirect_uri = settings.LINKEDIN_REDIRECT_URI
        self.api_base_url = "https://api.linkedin.com/v2"
        self.db_manager = DatabaseManager(settings.DATABASE_URL)
        
        # LinkedIn API endpoints
        self.endpoints = {
            "ugc_posts": f"{self.api_base_url}/ugcPosts",
            "assets": f"{self.api_base_url}/assets?action=registerUpload",
            "people": f"{self.api_base_url}/people/(id:{{person_urn}})",
            "organizations": f"{self.api_base_url}/organizations",
            "user_info": f"{self.api_base_url}/me"
        }
        
        # Rate limiting
        self.rate_limit_remaining = 100
        self.rate_limit_reset = None
        
        # Initialize HTTP client
        self.client = httpx.AsyncClient(
            timeout=30.0,
            headers={
                "User-Agent": "LinkedInContentAgent/1.0",
                "X-Restli-Protocol-Version": "2.0.0"
            }
        )
    
    async def _refresh_access_token(self, refresh_token: str) -> Optional[str]:
        """
        Refresh LinkedIn access token using refresh token.
        
        Args:
            refresh_token: The refresh token from initial auth
            
        Returns:
            New access token or None if failed
        """
        try:
            token_url = "https://www.linkedin.com/oauth/v2/accessToken"
            
            data = {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret
            }
            
            response = await self.client.post(token_url, data=data)
            
            if response.status_code == 200:
                token_data = response.json()
                new_access_token = token_data.get("access_token")
                
                if new_access_token:
                    self.access_token = new_access_token
                    logger.info("LinkedIn access token refreshed successfully")
                    return new_access_token
                else:
                    logger.error("No access token in refresh response")
                    return None
            else:
                logger.error(f"Token refresh failed: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Token refresh error: {e}")
            return None
    
    async def _get_authorization_headers(self) -> Dict[str, str]:
        """Get authorization headers for LinkedIn API requests"""
        if not self.access_token:
            raise ValueError("LinkedIn access token not configured")
        
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
    
    async def _handle_rate_limiting(self):
        """Handle LinkedIn API rate limiting"""
        if self.rate_limit_remaining <= 5:  # Low threshold
            if self.rate_limit_reset:
                reset_time = datetime.fromtimestamp(self.rate_limit_reset)
                wait_seconds = (reset_time - datetime.now()).total_seconds()
                
                if wait_seconds > 0:
                    logger.warning(f"Rate limit approaching. Waiting {wait_seconds} seconds")
                    await asyncio.sleep(min(wait_seconds, 300))  # Max 5 minutes wait
    
    async def _update_rate_limits(self, headers: Dict[str, str]):
        """Update rate limit information from response headers"""
        if "X-RateLimit-Remaining" in headers:
            self.rate_limit_remaining = int(headers["X-RateLimit-Remaining"])
        
        if "X-RateLimit-Reset" in headers:
            self.rate_limit_reset = int(headers["X-RateLimit-Reset"])
    
    async def _get_user_profile(self) -> Optional[Dict[str, Any]]:
        """
        Get current user's LinkedIn profile information.
        
        Returns:
            User profile data or None if failed
        """
        try:
            headers = await self._get_authorization_headers()
            
            response = await self.client.get(
                self.endpoints["user_info"],
                headers=headers
            )
            
            await self._update_rate_limits(response.headers)
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Failed to get user profile: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Error getting user profile: {e}")
            return None
    
    async def _get_person_urn(self) -> Optional[str]:
        """
        Get the person URN for the authenticated user.
        
        Returns:
            Person URN string or None if failed
        """
        try:
            profile = await self._get_user_profile()
            
            if profile and "id" in profile:
                return f"urn:li:person:{profile['id']}"
            else:
                logger.error("Could not extract person URN from profile")
                return None
                
        except Exception as e:
            logger.error(f"Error getting person URN: {e}")
            return None
    
    async def _upload_image(self, image_url: str) -> Optional[str]:
        """
        Upload image to LinkedIn and get image URN.
        
        Args:
            image_url: URL of the image to upload
            
        Returns:
            Image URN or None if failed
        """
        try:
            logger.info(f"Uploading image to LinkedIn: {image_url}")
            
            # Step 1: Register upload
            headers = await self._get_authorization_headers()
            
            register_data = {
                "registerUploadRequest": {
                    "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
                    "owner": await self._get_person_urn(),
                    "serviceRelationships": [
                        {
                            "relationshipType": "OWNER",
                            "identifier": "urn:li:userGeneratedContent"
                        }
                    ]
                }
            }
            
            register_response = await self.client.post(
                self.endpoints["assets"],
                headers=headers,
                json=register_data
            )
            
            if register_response.status_code != 200:
                logger.error(f"Image registration failed: {register_response.status_code} - {register_response.text}")
                return None
            
            register_data = register_response.json()
            
            # Extract upload URL and asset URN
            upload_url = register_data["value"]["uploadMechanism"]["com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"]["uploadUrl"]
            asset_urn = register_data["value"]["asset"]
            
            # Step 2: Download image from URL
            async with httpx.AsyncClient() as download_client:
                image_response = await download_client.get(image_url)
                
                if image_response.status_code != 200:
                    logger.error(f"Failed to download image: {image_response.status_code}")
                    return None
                
                image_data = image_response.content
            
            # Step 3: Upload image to LinkedIn
            upload_headers = {
                "Authorization": f"Bearer {self.access_token}",
            }
            
            upload_response = await self.client.put(
                upload_url,
                headers=upload_headers,
                content=image_data
            )
            
            if upload_response.status_code in [200, 201]:
                logger.info(f"Image uploaded successfully: {asset_urn}")
                return asset_urn
            else:
                logger.error(f"Image upload failed: {upload_response.status_code} - {upload_response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Image upload error: {e}")
            return None
    
    async def _create_text_post(self, content: str, author_urn: str) -> Optional[Dict[str, Any]]:
        """
        Create a text-only post on LinkedIn.
        
        Args:
            content: The post content text
            author_urn: LinkedIn author URN
            
        Returns:
            Post response data or None if failed
        """
        try:
            headers = await self._get_authorization_headers()
            
            post_data = {
                "author": author_urn,
                "lifecycleState": "PUBLISHED",
                "specificContent": {
                    "com.linkedin.ugc.ShareContent": {
                        "shareCommentary": {
                            "text": content
                        },
                        "shareMediaCategory": "NONE"
                    }
                },
                "visibility": {
                    "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
                }
            }
            
            response = await self.client.post(
                self.endpoints["ugc_posts"],
                headers=headers,
                json=post_data
            )
            
            await self._update_rate_limits(response.headers)
            
            if response.status_code == 201:
                post_data = response.json()
                logger.info(f"Text post created successfully: {post_data.get('id')}")
                return post_data
            else:
                logger.error(f"Text post creation failed: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Text post creation error: {e}")
            return None
    
    async def _create_image_post(self, content: str, image_urn: str, author_urn: str) -> Optional[Dict[str, Any]]:
        """
        Create a post with image on LinkedIn.
        
        Args:
            content: The post content text
            image_urn: LinkedIn image URN from upload
            author_urn: LinkedIn author URN
            
        Returns:
            Post response data or None if failed
        """
        try:
            headers = await self._get_authorization_headers()
            
            post_data = {
                "author": author_urn,
                "lifecycleState": "PUBLISHED",
                "specificContent": {
                    "com.linkedin.ugc.ShareContent": {
                        "shareCommentary": {
                            "text": content
                        },
                        "shareMediaCategory": "IMAGE",
                        "media": [
                            {
                                "status": "READY",
                                "description": {
                                    "text": "Professional business image"
                                },
                                "media": image_urn
                            }
                        ]
                    }
                },
                "visibility": {
                    "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
                }
            }
            
            response = await self.client.post(
                self.endpoints["ugc_posts"],
                headers=headers,
                json=post_data
            )
            
            await self._update_rate_limits(response.headers)
            
            if response.status_code == 201:
                post_data = response.json()
                logger.info(f"Image post created successfully: {post_data.get('id')}")
                return post_data
            else:
                logger.error(f"Image post creation failed: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Image post creation error: {e}")
            return None
    
    async def post_content(
        self, 
        content: str, 
        image_url: Optional[str] = None,
        author_urn: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Main method to post content to LinkedIn.
        
        Args:
            content: The post content text (1500 chars max for LinkedIn)
            image_url: Optional image URL to include
            author_urn: Optional author URN (uses authenticated user if not provided)
            
        Returns:
            Dictionary with post results and metadata
        """
        try:
            await self._handle_rate_limiting()
            
            logger.info("Starting LinkedIn post creation")
            
            # Validate content length
            if len(content) > 3000:  # LinkedIn's limit is ~3000 but we're conservative
                content = content[:2997] + "..."
                logger.warning("Content truncated for LinkedIn")
            
            # Get author URN
            if not author_urn:
                author_urn = await self._get_person_urn()
                if not author_urn:
                    raise ValueError("Could not determine LinkedIn author URN")
            
            # Handle image post if image provided
            if image_url:
                image_urn = await self._upload_image(image_url)
                
                if image_urn:
                    post_result = await self._create_image_post(content, image_urn, author_urn)
                else:
                    logger.warning("Image upload failed, falling back to text post")
                    post_result = await self._create_text_post(content, author_urn)
            else:
                # Text-only post
                post_result = await self._create_text_post(content, author_urn)
            
            if post_result:
                # Extract post ID and create view URL
                post_id = post_result.get("id")
                post_url = f"https://www.linkedin.com/feed/update/{post_id}" if post_id else None
                
                result = {
                    "success": True,
                    "post_id": post_id,
                    "post_url": post_url,
                    "posted_at": datetime.now().isoformat(),
                    "has_image": bool(image_url),
                    "content_length": len(content)
                }
                
                logger.info(f"LinkedIn post successful: {post_id}")
                return result
            else:
                raise Exception("Post creation failed - no response from LinkedIn API")
                
        except Exception as e:
            logger.error(f"LinkedIn posting failed: {e}")
            
            return {
                "success": False,
                "error": str(e),
                "posted_at": datetime.now().isoformat(),
                "has_image": bool(image_url)
            }
    
    async def get_post_metrics(self, post_id: str) -> Optional[Dict[str, Any]]:
        """
        Get engagement metrics for a LinkedIn post.
        
        Args:
            post_id: The LinkedIn post ID
            
        Returns:
            Post metrics or None if failed
        """
        try:
            headers = await self._get_authorization_headers()
            
            # Note: LinkedIn's analytics API is more complex and may require
            # additional permissions. This is a simplified version.
            metrics_url = f"{self.api_base_url}/socialActions/{post_id}"
            
            response = await self.client.get(metrics_url, headers=headers)
            
            await self._update_rate_limits(response.headers)
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.warning(f"Could not fetch post metrics: {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"Error fetching post metrics: {e}")
            return None
    
    async def validate_credentials(self) -> bool:
        """
        Validate LinkedIn API credentials and access token.
        
        Returns:
            True if credentials are valid, False otherwise
        """
        try:
            profile = await self._get_user_profile()
            return profile is not None and "id" in profile
            
        except Exception as e:
            logger.error(f"Credential validation failed: {e}")
            return False
    
    async def get_auth_url(self, state: str = None) -> str:
        """
        Generate LinkedIn OAuth2 authorization URL.
        
        Args:
            state: Optional state parameter for security
            
        Returns:
            LinkedIn authorization URL
        """
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": "r_liteprofile r_member_social w_member_social",  # Basic posting permissions
        }
        
        if state:
            params["state"] = state
        
        return f"https://www.linkedin.com/oauth/v2/authorization?{urlencode(params)}"
    
    async def exchange_code_for_token(self, code: str) -> Optional[Dict[str, Any]]:
        """
        Exchange authorization code for access token.
        
        Args:
            code: Authorization code from LinkedIn
            
        Returns:
            Token data or None if failed
        """
        try:
            token_url = "https://www.linkedin.com/oauth/v2/accessToken"
            
            data = {
                "grant_type": "authorization_code",
                "code": code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": self.redirect_uri
            }
            
            response = await self.client.post(token_url, data=data)
            
            if response.status_code == 200:
                token_data = response.json()
                
                # Update service with new token
                self.access_token = token_data.get("access_token")
                
                logger.info("Successfully exchanged code for access token")
                return token_data
            else:
                logger.error(f"Token exchange failed: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Token exchange error: {e}")
            return None
    
    async def get_user_organizations(self) -> List[Dict[str, Any]]:
        """
        Get organizations (companies) the user can post to.
        
        Returns:
            List of organization data
        """
        try:
            headers = await self._get_authorization_headers()
            
            # This endpoint requires additional permissions
            response = await self.client.get(
                self.endpoints["organizations"],
                headers=headers,
                params={"q": "isCompanyAdmin"}
            )
            
            if response.status_code == 200:
                organizations = response.json().get("elements", [])
                return organizations
            else:
                logger.warning(f"Could not fetch organizations: {response.status_code}")
                return []
                
        except Exception as e:
            logger.error(f"Error fetching organizations: {e}")
            return []
    
    async def post_to_organization(
        self, 
        content: str, 
        organization_urn: str,
        image_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Post content to a LinkedIn organization (company page).
        
        Args:
            content: The post content
            organization_urn: LinkedIn organization URN
            image_url: Optional image URL
            
        Returns:
            Post result data
        """
        try:
            # Similar to personal posts but with organization as author
            # Implementation would be similar to post_content but with org URN
            logger.info(f"Posting to organization: {organization_urn}")
            
            # This is a simplified version - actual implementation would
            # require the organization URN and proper permissions
            
            result = await self.post_content(
                content=content,
                image_url=image_url,
                author_urn=organization_urn
            )
            
            result["organization_urn"] = organization_urn
            return result
            
        except Exception as e:
            logger.error(f"Organization posting failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "organization_urn": organization_urn
            }
    
    async def test_connection(self) -> Dict[str, Any]:
        """
        Test LinkedIn API connection and permissions.
        
        Returns:
            Connection test results
        """
        try:
            # Test basic API access
            profile = await self._get_user_profile()
            can_post = await self.validate_credentials()
            organizations = await self.get_user_organizations()
            
            return {
                "connected": bool(profile),
                "can_post": can_post,
                "profile_accessible": bool(profile),
                "organizations_accessible": len(organizations) > 0,
                "rate_limit_remaining": self.rate_limit_remaining,
                "user_id": profile.get("id") if profile else None,
                "organizations_count": len(organizations)
            }
            
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return {
                "connected": False,
                "error": str(e)
            }
    
    async def close(self):
        """Close HTTP client connections"""
        await self.client.aclose()


# Global LinkedIn service instance
linkedin_service = LinkedInService()