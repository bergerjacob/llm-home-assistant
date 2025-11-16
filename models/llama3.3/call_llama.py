#!/usr/bin/env python3
"""
Dummy model script for llama3.3 - not yet implemented.
Returns a mock response for testing purposes.
"""
import logging

_LOGGER = logging.getLogger(__name__)


def query_llama(prompt: str, system_instruction: str = None) -> str:
    """
    Query llama3.3 model with a prompt (DUMMY - not implemented).

    Args:
        prompt (str): The user prompt to send to the model
        system_instruction (str, optional): System instruction to guide the model behavior

    Returns:
        str: A dummy response indicating the model is not yet implemented
    """
    _LOGGER.warning("Llama3.3 model is not yet implemented - returning dummy response")
    
    dummy_response = (
        "This is a dummy response from llama3.3 model. "
        "The model is not yet implemented. "
        f"Your prompt was: {prompt[:50]}..."
    )
    
    return dummy_response


