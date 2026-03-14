"""
LLM Service for Listing Analysis using Google Gemini

This module provides LLM-based verification of listings to ensure they match
the user's target description and don't contain negative keywords.
"""

import os
import base64
import json
import logging
from typing import Dict, Any, Optional
import httpx
from libs.common.settings import settings
from libs.common.log import logger

try:
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_core.messages import HumanMessage
    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False
    logger.warning("langchain-google-genai not available. LLM features will be disabled.")


def _download_image_as_base64(image_url: str) -> Optional[str]:
    """
    Download an image from URL and convert to base64.
    
    Args:
        image_url: URL of the image to download
        
    Returns:
        Base64-encoded image string or None if download fails
    """
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(image_url)
            response.raise_for_status()
            
            # Check if it's an image
            content_type = response.headers.get("content-type", "")
            if not content_type.startswith("image/"):
                logger.warning(f"URL {image_url} is not an image (content-type: {content_type})")
                return None
            
            # Encode to base64
            image_base64 = base64.b64encode(response.content).decode("utf-8")
            return image_base64
    except Exception as e:
        logger.error(f"Failed to download image from {image_url}: {e}")
        return None


def analyze_listing(
    image_url: Optional[str],
    title: str,
    price: float,
    description: Optional[str],
    target_description: str,
    negative_keywords: Optional[str] = None
) -> Dict[str, Any]:
    """
    Analyze a listing using Gemini LLM to verify it matches the target description
    and doesn't contain negative keywords.
    
    Args:
        image_url: URL of the listing image (optional)
        title: Listing title
        price: Listing price
        description: Listing description (optional)
        target_description: User's target description ("What I am looking for")
        negative_keywords: Comma-separated list of negative keywords to avoid
        
    Returns:
        Dict with:
            - score: 0-100 score from LLM
            - reasoning: Explanation from LLM
            - verified: Boolean indicating if listing passed verification
    """
    if not LANGCHAIN_AVAILABLE:
        logger.warning("LLM analysis requested but langchain-google-genai not available")
        return {
            "score": 0.0,
            "reasoning": "LLM service not available",
            "verified": False
        }
    
    # Get API key from environment
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        logger.error("GOOGLE_API_KEY not set in environment")
        return {
            "score": 0.0,
            "reasoning": "API key not configured",
            "verified": False
        }
    
    try:
        # Initialize Gemini
        llm = ChatGoogleGenerativeAI(
            model="gemini-1.5-flash",
            google_api_key=api_key,
            temperature=0.3
        )
        
        # Build prompt
        prompt_parts = []
        
        # Add image if available
        if image_url:
            image_base64 = _download_image_as_base64(image_url)
            if image_base64:
                prompt_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}
                })
        
        # Build text prompt
        text_prompt = f"""You are analyzing a marketplace listing to determine if it matches a user's requirements.

LISTING INFORMATION:
- Title: {title}
- Price: €{price:.2f}
- Description: {description or "No description provided"}

USER REQUIREMENTS:
- Target Description: {target_description}
"""
        
        if negative_keywords:
            keywords_list = [k.strip() for k in negative_keywords.split(",") if k.strip()]
            text_prompt += f"- Negative Keywords (AVOID if present): {', '.join(keywords_list)}\n"
        
        text_prompt += """
TASK:
1. Analyze if this listing matches the target description
2. Check if it contains any negative keywords (if provided)
3. Provide a score from 0-100 where:
   - 90-100: Perfect match, highly recommended
   - 70-89: Good match, recommended
   - 50-69: Partial match, consider carefully
   - 30-49: Poor match, likely not what user wants
   - 0-29: Bad match or contains negative keywords, avoid

4. Provide clear reasoning for your score

Respond in JSON format:
{
    "score": <number 0-100>,
    "reasoning": "<explanation>",
    "verified": <true if score >= 70, false otherwise>
}
"""
        
        prompt_parts.append(HumanMessage(content=text_prompt))
        
        # Call LLM
        response = llm.invoke(prompt_parts)
        
        # Parse response
        response_text = response.content.strip()
        
        # Try to extract JSON from response
        json_start = response_text.find("{")
        json_end = response_text.rfind("}") + 1
        
        if json_start >= 0 and json_end > json_start:
            json_text = response_text[json_start:json_end]
            result = json.loads(json_text)
            
            # Validate and normalize result
            score = float(result.get("score", 0))
            score = max(0, min(100, score))  # Clamp to 0-100
            
            reasoning = result.get("reasoning", "No reasoning provided")
            verified = result.get("verified", score >= 70)
            
            return {
                "score": score,
                "reasoning": reasoning,
                "verified": bool(verified)
            }
        else:
            # Fallback: try to extract score from text
            logger.warning(f"Could not parse JSON from LLM response: {response_text[:200]}")
            return {
                "score": 50.0,
                "reasoning": response_text[:500],
                "verified": False
            }
            
    except Exception as e:
        logger.error(f"Error in LLM analysis: {e}", exc_info=True)
        return {
            "score": 0.0,
            "reasoning": f"Error during analysis: {str(e)}",
            "verified": False
        }
