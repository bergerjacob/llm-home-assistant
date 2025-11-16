#!/usr/bin/env python3
import os
import argparse
import logging
from openai import OpenAI

_LOGGER = logging.getLogger(__name__)


def query_gpt4o(prompt: str, system_instruction: str = None) -> str:
    """
    Query OpenAI's GPT-4o model with a prompt.

    Args:
        prompt (str): The user prompt to send to the model
        system_instruction (str, optional): System instruction to guide the model behavior

    Returns:
        str: The model's response text

    Raises:
        RuntimeError: If OPENAI_API_KEY environment variable is not set
        Exception: If the API call fails
    """
    if "OPENAI_API_KEY" not in os.environ:
        raise RuntimeError(
            "Environment variable OPENAI_API_KEY is not set; please export it before running."
        )

    client = OpenAI()

    try:
        # Build messages list
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        _LOGGER.info(f"Sending prompt to GPT-4o: {prompt[:100]}...")

        # API call
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
        )

        result = response.choices[0].message.content
        _LOGGER.info(f"Received response from GPT-4o: {result[:100]}...")

        return result

    except Exception as e:
        _LOGGER.error(f"Error calling GPT-4o API: {e}")
        raise


def main() -> None:
    """CLI entry point for testing."""
    parser = argparse.ArgumentParser(
        description="Send a prompt to OpenAI's gpt-4o model."
    )
    parser.add_argument(
        "prompt", type=str, help="The prompt to send to the model."
    )
    parser.add_argument(
        "--system", type=str, help="System instruction (optional)", default=None
    )
    args = parser.parse_args()

    try:
        result = query_gpt4o(args.prompt, args.system)
        print(result)
    except Exception as e:
        print(f"An error occurred: {e}")
        exit(1)


if __name__ == "__main__":
    main()

