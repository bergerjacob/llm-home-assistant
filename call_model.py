"""
Model calling module for LLM Home Assistant.
"""
import json
import logging
import sys
import os
import subprocess

_LOGGER = logging.getLogger(__name__)

# Get the directory where this script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Set MODELS_DIR to be the 'models' directory inside this component's directory
MODELS_DIR = os.path.join(SCRIPT_DIR, "models")


def query_model(user_input, model_name="openai"):
    """
    Query the model with user input.

    Args:
        user_input (str): The text command from Home Assistant
        model_name (str): The model to use ("openai", "llama3.3", etc.). Defaults to "openai".

    Returns:
        str: The JSON string response from the model
    """

    # System instruction for the model
    system_instruction = "You are a smart home assistant. Output valid JSON only."

    _LOGGER.info(f"Querying model '{model_name}' with input: {user_input[:100]}...")

    try:
        # Model switcher - import and call the appropriate model
        if model_name == "openai":
            # Use venv activation to handle Python path correctly
            activate_script = os.path.join(MODELS_DIR, "openai", "env", "bin", "activate")
            openai_script_path = os.path.join(MODELS_DIR, "openai", "call_gpt4o.py")

            if not os.path.exists(activate_script):
                raise ImportError(f"Could not find venv activate script at {activate_script}")
            if not os.path.exists(openai_script_path):
                raise ImportError(f"Could not find OpenAI script at {openai_script_path}")
            env = os.environ.copy()
            if "OPENAI_API_KEY" not in env:
                _LOGGER.warning("OPENAI_API_KEY not set in environment")

            # Build command to activate venv and run Python
            # Pass arguments via environment variables to avoid escaping issues
            env["LLM_USER_INPUT"] = user_input
            env["LLM_SYSTEM_INSTRUCTION"] = system_instruction or ""

            cmd = (
                f"source {activate_script} && "
                f"python -c \""
                f"import sys, os; "
                f"sys.path.insert(0, '{MODELS_DIR}/openai'); "
                f"from call_gpt4o import query_gpt4o; "
                f"user_input = os.environ['LLM_USER_INPUT']; "
                f"system_inst = os.environ.get('LLM_SYSTEM_INSTRUCTION') or None; "
                f"response = query_gpt4o(user_input, system_inst); "
                f"print(response, end='')\""
            )

            result = subprocess.run(
                ["/bin/bash", "-c", cmd],
                capture_output=True,
                text=True,
                env=env,
                timeout=60
            )

            if result.returncode != 0:
                raise RuntimeError(f"Error calling OpenAI script: {result.stderr}")

            response_text = result.stdout.strip()

        elif model_name == "llama3.3":
            # Import and use the dummy llama model
            sys.path.insert(0, os.path.join(MODELS_DIR, "llama3.3"))
            from call_llama import query_llama

            response_text = query_llama(user_input, system_instruction)

        else:
            _LOGGER.warning(f"Unknown model '{model_name}', defaulting to OpenAI")
            # Use venv activation (same as openai case above)
            activate_script = os.path.join(MODELS_DIR, "openai", "env", "bin", "activate")
            openai_script_path = os.path.join(MODELS_DIR, "openai", "call_gpt4o.py")
            env = os.environ.copy()

            # Pass arguments via environment variables (same as above)
            env["LLM_USER_INPUT"] = user_input
            env["LLM_SYSTEM_INSTRUCTION"] = system_instruction or ""

            cmd = (
                f"source {activate_script} && "
                f"python -c \""
                f"import sys, os; "
                f"sys.path.insert(0, '{MODELS_DIR}/openai'); "
                f"from call_gpt4o import query_gpt4o; "
                f"user_input = os.environ['LLM_USER_INPUT']; "
                f"system_inst = os.environ.get('LLM_SYSTEM_INSTRUCTION') or None; "
                f"response = query_gpt4o(user_input, system_inst); "
                f"print(response, end='')\""
            )

            result = subprocess.run(
                ["/bin/bash", "-c", cmd],
                capture_output=True,
                text=True,
                env=env,
                timeout=60
            )

            if result.returncode != 0:
                raise RuntimeError(f"Error calling OpenAI script: {result.stderr}")

            response_text = result.stdout.strip()

        _LOGGER.info(f"Model '{model_name}' response: {response_text[:200]}...")

        # Try to parse the response as JSON, if it's not already JSON, wrap it
        try:
            # Try to parse as JSON first
            json.loads(response_text)
            result = response_text
        except (json.JSONDecodeError, TypeError):
            # If not JSON, wrap it in a JSON structure
            _LOGGER.warning("Model response is not valid JSON, wrapping it")
            result = json.dumps({
                "action": "response",
                "message": response_text
            })

        return result

    except Exception as e:
        _LOGGER.error(f"Error calling model '{model_name}': {e}", exc_info=True)
        # Return error as JSON
        return json.dumps({
            "action": "error",
            "message": f"Error calling model: {str(e)}"
        })


# Keep this block so you can still test it via CLI if needed
if __name__ == "__main__":
    import sys
    input_text = sys.argv[1] if len(sys.argv) > 1 else "Test command"
    model = sys.argv[2] if len(sys.argv) > 2 else "openai"
    print(query_model(input_text, model))
