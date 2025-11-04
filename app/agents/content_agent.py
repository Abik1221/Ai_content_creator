import logging
from typing import Dict, List, Optional, Any
from langgraph.graph import StateGraph, START, END
from langchain.schema import BaseMessage, HumanMessage, SystemMessage, AIMessage
from langchain_openai import ChatOpenAI
from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder
from pydantic import BaseModel, Field
import asyncio
from datetime import datetime

from app.models.schemas import AgentState
from app.core.config import settings

logger = logging.getLogger(__name__)


class ContentGenerationResult(BaseModel):
    """Result model for content generation"""
    final_content: str = Field(..., description="Final approved content")
    draft_content: str = Field(..., description="Initial draft content")
    hashtags: List[str] = Field(default_factory=list, description="Relevant hashtags")
    image_prompt: Optional[str] = Field(None, description="Generated image prompt")
    status: str = Field(..., description="Generation status")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Generation metadata")


class ContentGenerationAgent:
    """
    LangGraph agent for LinkedIn content generation.
    
    This agent uses a stateful workflow to:
    1. Research the topic and company context
    2. Write professional content drafts
    3. Generate image prompts
    4. Review and refine content
    """
    
    def __init__(self):
        self.llm = ChatOpenAI(
            model=settings.OPENAI_MODEL,
            temperature=0.7,
            openai_api_key=settings.OPENAI_API_KEY
        )
        self.graph = self._build_graph()
        self.compiled_graph = None
        self._compile_graph()
    
    def _build_graph(self) -> StateGraph:
        """Build the LangGraph state machine for content generation"""
        workflow = StateGraph(AgentState)
        
        # Add nodes to the graph
        workflow.add_node("research_topic", self.research_topic)
        workflow.add_node("generate_draft", self.generate_draft)
        workflow.add_node("generate_image_prompt", self.generate_image_prompt)
        workflow.add_node("review_content", self.review_content)
        workflow.add_node("finalize_content", self.finalize_content)
        
        # Define the workflow edges
        workflow.add_edge(START, "research_topic")
        workflow.add_edge("research_topic", "generate_draft")
        workflow.add_edge("generate_draft", "generate_image_prompt")
        workflow.add_edge("generate_image_prompt", "review_content")
        workflow.add_edge("review_content", "finalize_content")
        workflow.add_edge("finalize_content", END)
        
        return workflow
    
    def _compile_graph(self):
        """Compile the graph for execution"""
        try:
            self.compiled_graph = self.graph.compile()
            logger.info("Content generation graph compiled successfully")
        except Exception as e:
            logger.error(f"Failed to compile graph: {e}")
            raise
    
    async def research_topic(self, state: AgentState) -> Dict[str, Any]:
        """Research the topic and gather relevant information"""
        logger.info(f"Researching topic: {state['topic']}")
        
        research_prompt = ChatPromptTemplate.from_messages([
            SystemMessage(content="""
            You are a professional content researcher specializing in LinkedIn content for businesses.
            Your task is to research the given topic and company context to gather relevant information
            that will help create engaging, professional LinkedIn content.
            
            Consider:
            - Industry trends and insights
            - Target audience interests
            - Company's unique value proposition
            - Relevant data or statistics
            - Current events related to the topic
            """),
            HumanMessage(content=f"""
            Company Information:
            {state['company_info']}
            
            Topic to Research:
            {state['topic']}
            
            Content Style: {state['style']}
            
            Please provide comprehensive research notes that will help write compelling LinkedIn content.
            Focus on key points, interesting facts, and engaging angles.
            """)
        ])
        
        try:
            research_chain = research_prompt | self.llm
            response = await research_chain.ainvoke({})
            
            return {
                "research_notes": response.content,
                "status": "researched"
            }
        except Exception as e:
            logger.error(f"Research failed: {e}")
            return {
                "research_notes": f"Research unavailable: {str(e)}",
                "status": "research_failed",
                "error": str(e)
            }
    
    async def generate_draft(self, state: AgentState) -> Dict[str, Any]:
        """Generate initial content draft based on research"""
        logger.info("Generating content draft")
        
        # Define style-specific guidelines
        style_guides = {
            "professional": "Professional, authoritative, industry-focused tone",
            "casual": "Conversational, friendly, approachable tone",
            "inspirational": "Motivational, uplifting, visionary tone",
            "technical": "Detailed, precise, expertise-focused tone",
            "storytelling": "Narrative-driven, personal, engaging tone"
        }
        
        style_guide = style_guides.get(state['style'], "Professional, engaging tone")
        
        draft_prompt = ChatPromptTemplate.from_messages([
            SystemMessage(content=f"""
            You are an expert LinkedIn content writer. Create compelling {state['style']} content 
            that engages professionals and drives conversation.
            
            Style Guidelines: {style_guide}
            
            Content Requirements:
            - Length: 3-5 paragraphs (optimal for LinkedIn engagement)
            - Include a hook in the first sentence
            - Add value with insights or actionable advice
            - End with a question to encourage comments
            - Use appropriate professional language
            - Include 3-5 relevant hashtags at the end
            
            Structure:
            1. Engaging hook/opening
            2. Key insights/value proposition
            3. Supporting details or examples
            4. Call-to-action or engaging question
            5. Relevant hashtags
            """),
            HumanMessage(content=f"""
            Company Context:
            {state['company_info']}
            
            Topic: {state['topic']}
            
            Research Notes:
            {state.get('research_notes', 'No specific research notes available.')}
            
            Please generate compelling LinkedIn content that aligns with the company context and topic.
            """)
        ])
        
        try:
            draft_chain = draft_prompt | self.llm
            response = await draft_chain.ainvoke({})
            
            # Extract hashtags from the content
            content = response.content
            hashtags = self._extract_hashtags(content)
            
            return {
                "draft_content": content,
                "hashtags": hashtags,
                "status": "draft_generated"
            }
        except Exception as e:
            logger.error(f"Draft generation failed: {e}")
            return {
                "draft_content": f"Content generation failed: {str(e)}",
                "status": "draft_failed",
                "error": str(e)
            }
    
    async def generate_image_prompt(self, state: AgentState) -> Dict[str, Any]:
        """Generate image prompt for visual content"""
        logger.info("Generating image prompt")
        
        image_prompt_template = ChatPromptTemplate.from_messages([
            SystemMessage(content="""
            You are an expert at creating image prompts for AI image generation.
            Create detailed, descriptive prompts that would generate professional,
            engaging images suitable for LinkedIn content.
            
            Focus on:
            - Professional business imagery
            - Abstract concepts related to the content
            - Clean, modern aesthetics
            - Brand-appropriate visuals
            - High-quality, realistic or professional illustration style
            """),
            HumanMessage(content=f"""
            Content Topic: {state['topic']}
            
            Generated Content:
            {state.get('draft_content', 'No content available')}
            
            Company Context:
            {state['company_info']}
            
            Style: {state['style']}
            
            Please create a detailed image generation prompt that visually represents this content
            in a professional, engaging way suitable for LinkedIn.
            """)
        ])
        
        try:
            image_chain = image_prompt_template | self.llm
            response = await image_chain.ainvoke({})
            
            return {
                "image_prompt": response.content,
                "status": "image_prompt_generated"
            }
        except Exception as e:
            logger.error(f"Image prompt generation failed: {e}")
            return {
                "image_prompt": f"Professional business image related to {state['topic']}",
                "status": "image_prompt_fallback",
                "error": str(e)
            }
    
    async def review_content(self, state: AgentState) -> Dict[str, Any]:
        """Review and refine the generated content"""
        logger.info("Reviewing and refining content")
        
        review_prompt = ChatPromptTemplate.from_messages([
            SystemMessage(content="""
            You are a senior content editor specializing in LinkedIn professional content.
            Your task is to review and refine the generated content to ensure:
            - Professional quality and tone
            - Clarity and impact
            - Engagement potential
            - Appropriate length and structure
            - Error-free writing
            
            Provide specific improvements and a final polished version.
            """),
            HumanMessage(content=f"""
            Original Draft Content:
            {state.get('draft_content', 'No content available')}
            
            Topic: {state['topic']}
            Company Context: {state['company_info']}
            Style: {state['style']}
            Hashtags: {state.get('hashtags', [])}
            
            Please review this content and provide:
            1. Specific improvement suggestions
            2. A final polished version of the content
            3. Any structural or tonal adjustments needed
            
            Return the improved content ready for LinkedIn posting.
            """)
        ])
        
        try:
            review_chain = review_prompt | self.llm
            response = await review_chain.ainvoke({})
            
            # Extract the final content (assuming it's the main response)
            final_content = response.content
            
            return {
                "final_content": final_content,
                "review_notes": "Content reviewed and polished",
                "status": "content_reviewed"
            }
        except Exception as e:
            logger.error(f"Content review failed: {e}")
            # Fall back to draft content if review fails
            return {
                "final_content": state.get('draft_content', 'Content unavailable'),
                "review_notes": f"Review failed: {str(e)}",
                "status": "review_failed",
                "error": str(e)
            }
    
    async def finalize_content(self, state: AgentState) -> Dict[str, Any]:
        """Finalize the content and prepare for output"""
        logger.info("Finalizing content generation")
        
        # Ensure we have final content
        final_content = state.get('final_content') or state.get('draft_content')
        
        if not final_content:
            raise ValueError("No content generated in the workflow")
        
        # Ensure hashtags are properly formatted
        hashtags = state.get('hashtags', [])
        if not hashtags:
            # Generate fallback hashtags
            hashtags = self._generate_fallback_hashtags(state['topic'])
        
        return {
            "final_content": final_content,
            "hashtags": hashtags,
            "image_prompt": state.get('image_prompt'),
            "status": "completed",
            "completed_at": datetime.now().isoformat()
        }
    
    def _extract_hashtags(self, content: str) -> List[str]:
        """Extract hashtags from content"""
        import re
        hashtags = re.findall(r'#(\w+)', content)
        
        # Remove duplicates and limit to 5
        unique_hashtags = list(set(hashtags))[:5]
        
        # If no hashtags found, return empty list (they'll be generated later)
        return unique_hashtags
    
    def _generate_fallback_hashtags(self, topic: str) -> List[str]:
        """Generate fallback hashtags based on topic"""
        # Simple keyword-based hashtag generation
        # In production, this could be more sophisticated
        base_hashtags = ["LinkedIn", "Professional", "Business"]
        
        # Add topic-specific hashtags
        topic_keywords = topic.lower().split()
        topic_hashtags = [f"{word.capitalize()}" for word in topic_keywords[:3]]
        
        return base_hashtags + topic_hashtags
    
    async def generate_content(
        self,
        company_info: str,
        topic: str,
        style: str = "professional",
        target_audience: Optional[str] = None,
        content_length: str = "medium"
    ) -> ContentGenerationResult:
        """
        Main method to generate LinkedIn content.
        
        Args:
            company_info: Information about the company
            topic: Content topic or theme
            style: Writing style (professional, casual, inspirational, etc.)
            target_audience: Optional target audience description
            content_length: Content length (short, medium, long)
            
        Returns:
            ContentGenerationResult with final content and metadata
        """
        logger.info(f"Starting content generation - Topic: {topic}, Style: {style}")
        
        try:
            # Initialize state
            initial_state = AgentState(
                company_info=company_info,
                topic=topic,
                style=style,
                target_audience=target_audience,
                content_length=content_length,
                draft_content="",
                final_content="",
                image_prompt="",
                hashtags=[],
                status="started",
                error=None
            )
            
            # Execute the graph
            if not self.compiled_graph:
                self._compile_graph()
            
            final_state = await self.compiled_graph.ainvoke(initial_state.dict())
            
            # Create result object
            result = ContentGenerationResult(
                final_content=final_state["final_content"],
                draft_content=final_state.get("draft_content", ""),
                hashtags=final_state.get("hashtags", []),
                image_prompt=final_state.get("image_prompt"),
                status=final_state.get("status", "completed"),
                metadata={
                    "topic": topic,
                    "style": style,
                    "content_length": content_length,
                    "generated_at": datetime.now().isoformat(),
                    "workflow_steps": list(self.graph.nodes)
                }
            )
            
            logger.info("Content generation completed successfully")
            return result
            
        except Exception as e:
            logger.error(f"Content generation workflow failed: {e}")
            
            # Return error result
            return ContentGenerationResult(
                final_content=f"Content generation failed: {str(e)}",
                draft_content="",
                hashtags=[],
                image_prompt=None,
                status="failed",
                metadata={"error": str(e), "generated_at": datetime.now().isoformat()}
            )
    
    async def generate_multiple_variations(
        self,
        company_info: str,
        topic: str,
        style: str = "professional",
        variations: int = 3
    ) -> List[ContentGenerationResult]:
        """Generate multiple content variations"""
        logger.info(f"Generating {variations} content variations for topic: {topic}")
        
        tasks = []
        for i in range(variations):
            # Slightly vary the temperature for different results
            original_temp = self.llm.temperature
            self.llm.temperature = min(0.7 + (i * 0.1), 0.9)  # Cap at 0.9
            
            task = self.generate_content(company_info, topic, style)
            tasks.append(task)
            
            # Reset temperature
            self.llm.temperature = original_temp
        
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Filter out exceptions
            valid_results = []
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"Variation {i} failed: {result}")
                else:
                    valid_results.append(result)
            
            logger.info(f"Generated {len(valid_results)} successful variations")
            return valid_results
            
        except Exception as e:
            logger.error(f"Multiple variations generation failed: {e}")
            return []


# Global agent instance
content_agent = ContentGenerationAgent()