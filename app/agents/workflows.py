"""
Workflow definitions and node implementations for the LangGraph agent.
Separated from the main agent class for better organization and testing.
"""

import logging
from typing import Dict, Any, List, Optional
from langchain.schema import BaseMessage
from langchain_openai import ChatOpenAI

from app.agents.prompts import (
    CONTENT_STRATEGIST_SYSTEM,
    TOPIC_RESEARCH_PROMPT,
    CONTENT_DRAFT_PROMPT,
    CONTENT_REVIEW_PROMPT,
    IMAGE_PROMPT_PROMPT,
    HASHTAG_PROMPT,
    get_industry_guidelines,
    get_style_template,
    create_anti_hallucination_reminder,
    QUALITY_CHECKLIST
)
from app.core.config import settings

logger = logging.getLogger(__name__)


class WorkflowNodes:
    """
    Implementation of individual workflow nodes for the content generation graph.
    Each method represents a node in the LangGraph state machine.
    """
    
    def __init__(self):
        self.llm = ChatOpenAI(
            model=settings.OPENAI_MODEL,
            temperature=0.7,  # Balanced for creativity and factuality
            openai_api_key=settings.OPENAI_API_KEY
        )
    
    async def research_topic(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Node: Research and analyze the topic based on company context"""
        logger.info(f"Researching topic: {state['topic']}")
        
        try:
            # Add anti-hallucination reminder to context
            anti_hallucination_reminder = create_anti_hallucination_reminder(state['company_info'])
            
            research_chain = TOPIC_RESEARCH_PROMPT | self.llm
            response = await research_chain.ainvoke({
                "company_info": state['company_info'],
                "topic": state['topic'],
                "style": state['style'],
                "anti_hallucination_reminder": anti_hallucination_reminder
            })
            
            return {
                "research_notes": response.content,
                "status": "researched",
                "workflow_step": "topic_research"
            }
            
        except Exception as e:
            logger.error(f"Research node failed: {e}")
            return {
                "research_notes": f"Research limited due to: {str(e)}",
                "status": "research_limited",
                "error": str(e),
                "workflow_step": "topic_research"
            }
    
    async def generate_draft(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Node: Generate initial content draft with fact checking"""
        logger.info("Generating content draft with fact checking")
        
        try:
            # Get industry and style guidelines
            industry_guidelines = get_industry_guidelines(state.get('industry', 'general'))
            style_template = get_style_template(state['style'])
            
            draft_chain = CONTENT_DRAFT_PROMPT | self.llm
            response = await draft_chain.ainvoke({
                "company_info": state['company_info'],
                "topic": state['topic'],
                "research_notes": state.get('research_notes', ''),
                "style": state['style'],
                "target_audience": state.get('target_audience', 'professionals'),
                "industry_guidelines": industry_guidelines,
                "style_template": style_template
            })
            
            # Extract hashtags
            hashtags = self._extract_hashtags(response.content)
            
            return {
                "draft_content": response.content,
                "hashtags": hashtags,
                "status": "draft_generated",
                "workflow_step": "content_drafting"
            }
            
        except Exception as e:
            logger.error(f"Draft generation node failed: {e}")
            return {
                "draft_content": f"Content generation challenged: {str(e)}",
                "hashtags": [],
                "status": "draft_failed",
                "error": str(e),
                "workflow_step": "content_drafting"
            }
    
    async def generate_image_prompt(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Node: Create professional image prompts"""
        logger.info("Generating professional image prompts")
        
        try:
            image_chain = IMAGE_PROMPT_PROMPT | self.llm
            response = await image_chain.ainvoke({
                "topic": state['topic'],
                "company_info": state['company_info'],
                "style": state['style'],
                "content": state.get('draft_content', '')
            })
            
            return {
                "image_prompt": response.content,
                "status": "image_prompt_created",
                "workflow_step": "image_prompt_generation"
            }
            
        except Exception as e:
            logger.error(f"Image prompt node failed: {e}")
            return {
                "image_prompt": f"Professional business image related to {state['topic']}",
                "status": "image_prompt_fallback",
                "error": str(e),
                "workflow_step": "image_prompt_generation"
            }
    
    async def review_content(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Node: Review and fact-check the generated content"""
        logger.info("Reviewing content for accuracy and quality")
        
        try:
            review_chain = CONTENT_REVIEW_PROMPT | self.llm
            response = await review_chain.ainvoke({
                "draft_content": state.get('draft_content', ''),
                "company_info": state['company_info'],
                "topic": state['topic'],
                "style": state['style']
            })
            
            # Verify the reviewed content maintains quality
            quality_check = await self._perform_quality_check(
                response.content, state['company_info']
            )
            
            if not quality_check["is_acceptable"]:
                logger.warning("Content review failed quality check, using fallback")
                # Use original draft with disclaimer
                final_content = state.get('draft_content', '') + "\n\n[Content reviewed for accuracy]"
            else:
                final_content = response.content
            
            return {
                "final_content": final_content,
                "review_notes": quality_check["feedback"],
                "status": "content_reviewed",
                "workflow_step": "content_review",
                "quality_check_passed": quality_check["is_acceptable"]
            }
            
        except Exception as e:
            logger.error(f"Content review node failed: {e}")
            # Fall back to draft content with review notice
            return {
                "final_content": state.get('draft_content', '') + "\n\n[Content review incomplete]",
                "review_notes": f"Review failed: {str(e)}",
                "status": "review_limited",
                "error": str(e),
                "workflow_step": "content_review",
                "quality_check_passed": False
            }
    
    async def finalize_content(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Node: Finalize content and prepare for delivery"""
        logger.info("Finalizing content generation workflow")
        
        try:
            # Ensure we have the best available content
            final_content = state.get('final_content') or state.get('draft_content')
            
            if not final_content:
                raise ValueError("No content generated in workflow")
            
            # Ensure hashtags are appropriate
            hashtags = state.get('hashtags', [])
            if not hashtags:
                hashtags = await self._generate_appropriate_hashtags(
                    state['topic'], state.get('industry', 'general')
                )
            
            # Create workflow summary
            workflow_summary = self._create_workflow_summary(state)
            
            return {
                "final_content": final_content,
                "hashtags": hashtags,
                "image_prompt": state.get('image_prompt'),
                "status": "completed",
                "workflow_summary": workflow_summary,
                "workflow_step": "finalization",
                "quality_assurance": QUALITY_CHECKLIST
            }
            
        except Exception as e:
            logger.error(f"Finalization node failed: {e}")
            return {
                "final_content": "Content generation could not be completed successfully.",
                "hashtags": [],
                "status": "failed",
                "error": str(e),
                "workflow_step": "finalization"
            }
    
    async def _perform_quality_check(self, content: str, company_info: str) -> Dict[str, Any]:
        """Internal method to perform quality and fact-checking"""
        try:
            # Simple quality checks - in production, this could be more sophisticated
            checks = {
                "has_company_reference": any(keyword in content.lower() for keyword in ['we', 'our', 'company', 'team']),
                "has_engagement_element": any(keyword in content.lower() for keyword in ['?', 'comment', 'share', 'thoughts']),
                "appropriate_length": 100 <= len(content) <= 2000,
                "has_hashtags": '#' in content
            }
            
            passed_checks = sum(checks.values())
            total_checks = len(checks)
            quality_score = passed_checks / total_checks
            
            feedback = f"Quality score: {quality_score:.0%}. Checks passed: {passed_checks}/{total_checks}"
            
            return {
                "is_acceptable": quality_score >= 0.6,  # 60% threshold
                "score": quality_score,
                "feedback": feedback,
                "detailed_checks": checks
            }
            
        except Exception as e:
            logger.error(f"Quality check failed: {e}")
            return {
                "is_acceptable": True,  # Default to acceptable if check fails
                "score": 0.5,
                "feedback": f"Quality check incomplete: {str(e)}",
                "detailed_checks": {}
            }
    
    async def _generate_appropriate_hashtags(self, topic: str, industry: str) -> List[str]:
        """Generate professional hashtags when none are provided"""
        try:
            hashtag_chain = HASHTAG_PROMPT | self.llm
            response = await hashtag_chain.ainvoke({
                "topic": topic,
                "industry": industry,
                "style": "professional",
                "content_preview": topic[:100]  # Preview for context
            })
            
            # Extract hashtags from response
            hashtags = self._extract_hashtags(response.content)
            return hashtags[:5]  # Limit to 5 hashtags
            
        except Exception as e:
            logger.error(f"Hashtag generation failed: {e}")
            # Fallback hashtags
            return ["LinkedIn", "Professional", "Business", "Industry", "Career"]
    
    def _extract_hashtags(self, content: str) -> List[str]:
        """Extract hashtags from content"""
        import re
        hashtags = re.findall(r'#(\w+)', content)
        return list(set(hashtags))[:5]  # Remove duplicates and limit
    
    def _create_workflow_summary(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Create a summary of the workflow execution"""
        steps_completed = []
        errors_encountered = []
        
        # Track completed steps
        for step in ['research_topic', 'generate_draft', 'generate_image_prompt', 'review_content']:
            if step in state.get('workflow_step', ''):
                steps_completed.append(step)
        
        # Track errors
        if state.get('error'):
            errors_encountered.append(state['error'])
        
        return {
            "total_steps": len(steps_completed),
            "steps_completed": steps_completed,
            "errors_encountered": errors_encountered,
            "final_status": state.get('status', 'unknown'),
            "has_image_prompt": bool(state.get('image_prompt')),
            "hashtags_count": len(state.get('hashtags', [])),
            "content_length": len(state.get('final_content', '') or state.get('draft_content', ''))
        }


# Workflow configuration
WORKFLOW_CONFIG = {
    "max_retries": 2,
    "retry_delay": 1.0,
    "timeout": 300,  # 5 minutes
    "quality_threshold": 0.6,
    "content_length_limits": {
        "min": 100,
        "max": 2000
    }
}


def get_workflow_edges() -> Dict[str, str]:
    """Define the workflow edges for the state graph"""
    return {
        "start": "research_topic",
        "research_topic": "generate_draft", 
        "generate_draft": "generate_image_prompt",
        "generate_image_prompt": "review_content",
        "review_content": "finalize_content",
        "finalize_content": "end"
    }


def get_fallback_handlers():
    """Get fallback handlers for workflow failures"""
    return {
        "research_topic": lambda state: {
            "research_notes": "Using basic topic analysis due to research limitations.",
            "status": "research_fallback",
            "workflow_step": "topic_research"
        },
        "generate_draft": lambda state: {
            "draft_content": f"Professional content about {state.get('topic', 'the topic')} based on available information.",
            "hashtags": ["Professional", "Business"],
            "status": "draft_fallback", 
            "workflow_step": "content_drafting"
        },
        "review_content": lambda state: {
            "final_content": state.get('draft_content', 'Content generation completed with review limitations.'),
            "review_notes": "Content review was limited, using original draft.",
            "status": "review_fallback",
            "workflow_step": "content_review"
        }
    }