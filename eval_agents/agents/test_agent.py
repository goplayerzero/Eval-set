"""TestAgent

Runs integration tests on cloned repositories using Claude Code within Docker containers.
Uses AI to dynamically discover, setup, and execute tests with detailed reporting.
"""

import os
import sys
import re
import json
import time
import tempfile
import subprocess
from typing import Dict, List, Any, Optional, Union
import docker
from anthropic import Anthropic
from dotenv import load_dotenv
from dataclasses import dataclass

from eval_agents.core.utils import update_test_results, DEFAULT_DB_NAME

import logging

# Load environment variables (.env optional)
load_dotenv()

# Configure logger
logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

# Get API key from environment
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY")

@dataclass
class TestAgent:
    """Agent that runs integration tests for repositories using Claude Code.
    
    Uses Claude Code to dynamically discover, set up, and run tests in the context
    of each repository, then processes and stores the results.
    """
    
    db_name: str = DEFAULT_DB_NAME
    claude_api_key: str = CLAUDE_API_KEY
    max_retries: int = 3
    
    def __post_init__(self):
        """Initialize Docker client and Claude API client."""
        # Initialize Docker client
        try:
            self.docker_client = docker.from_env()
            logger.info(f"Connected to Docker: {self.docker_client.version()['Version']}")
        except Exception as e:
            logger.info(f"Error connecting to Docker: {str(e)}")
            raise RuntimeError("Docker must be running to use TestAgent")
        
        # Initialize Claude API client
        self.claude_api_key = os.getenv("CLAUDE_API_KEY")
        if not self.claude_api_key:
            logger.info("Warning: CLAUDE_API_KEY not set in environment")
            logger.info("              Tests will not be able to run without API authentication")
        else:
            logger.info(f"Using Claude API key: {self.claude_api_key[:8]}...")
            self.claude_client = Anthropic(api_key=self.claude_api_key)
    
    def ask_claude(self, prompt: str, system_prompt: str = None) -> str:
        """Send a prompt to Claude API and get the response.
        
        Args:
            prompt: The prompt to send to Claude
            system_prompt: Optional system prompt
            
        Returns:
            Claude's response as a string
        """
        try:
            # Create message parameters
            params = {
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 4000,
                "temperature": 0,
                "messages": [{"role": "user", "content": prompt}]
            }
            
            # Add system prompt if provided
            if system_prompt:
                params["system"] = system_prompt
                
            # Call the API
            message = self.claude_client.messages.create(**params)
            return message.content[0].text
        except Exception as e:
            logger.info(f"Error calling Claude API: {str(e)}")
            return ""
    
    def install_claude_code(self, container_id: str) -> bool:
        """Install the Claude SDK in the container using direct pip installation for Python Alpine.
        
        Args:
            container_id: ID of the container to install Claude SDK in
            
        Returns:
            True if installation succeeded, False otherwise
        """
        try:
            container = self.docker_client.containers.get(container_id)
            logger.info("Installing Anthropic SDK in container...")
            
            # Since we're using a Python Alpine container, we can directly use pip
            # First, update package lists and install build dependencies
            logger.info("Updating package lists and installing build dependencies...")
            container.exec_run(["apk", "update"])
            container.exec_run(["apk", "add", "--no-cache", "gcc", "musl-dev", "python3-dev", "libffi-dev", "openssl-dev"])
            
            # Install Anthropic SDK via pip
            logger.info("Installing Anthropic SDK via pip...")
            exit_code, output = container.exec_run(["pip", "install", "--no-cache-dir", "anthropic"])
            
            if exit_code != 0:
                logger.info(f"Error installing Anthropic SDK with pip: {output.decode('utf-8', errors='replace')}")
                
                # Try with pip3 explicitly
                exit_code, output = container.exec_run(["pip3", "install", "--no-cache-dir", "anthropic"])
                
                if exit_code != 0:
                    logger.info(f"Error installing Anthropic SDK with pip3: {output.decode('utf-8', errors='replace')}")
                    return False
            
            # Set environment variable for Claude API key
            container.exec_run(["sh", "-c", f"echo 'export ANTHROPIC_API_KEY={self.claude_api_key}' >> /root/.profile"])
            container.exec_run(["sh", "-c", f"echo 'export ANTHROPIC_API_KEY={self.claude_api_key}' >> /etc/profile"])
            
            # Verify installation
            test_script = "import anthropic; logger.info('Anthropic SDK installed successfully');"
            exit_code, output = container.exec_run(["python", "-c", test_script], environment={"ANTHROPIC_API_KEY": self.claude_api_key})
            
            if exit_code != 0:
                # Try with python3 command explicitly
                exit_code, output = container.exec_run(["python3", "-c", test_script], environment={"ANTHROPIC_API_KEY": self.claude_api_key})
                
                if exit_code != 0:
                    logger.info(f"Anthropic SDK verification failed: {output.decode('utf-8', errors='replace')}")
                    
                    # One more attempt with a more direct approach
                    logger.info("Trying alternative installation method...")
                    install_script = """#!/bin/sh
                    set -e
                    apk update
                    apk add --no-cache gcc musl-dev python3-dev libffi-dev openssl-dev
                    pip install --no-cache-dir --upgrade pip
                    pip install --no-cache-dir anthropic
                    python -c "import anthropic; logger.info('Anthropic SDK installed successfully')"
                    """
                    
                    script_path = "/tmp/install_claude.sh"
                    container.exec_run(["sh", "-c", f"echo '{install_script}' > {script_path}"])
                    container.exec_run(["chmod", "+x", script_path])
                    
                    exit_code, output = container.exec_run(["sh", "-c", f"{script_path}"])
                    if exit_code != 0:
                        logger.info(f"Alternative installation also failed: {output.decode('utf-8', errors='replace')}")
                        return False
            
            logger.info("Anthropic SDK installed successfully")
            return True
            
        except Exception as e:
            logger.info(f"Error installing Anthropic SDK: {str(e)}")
            return False
    
    def analyze_repo_structure(self, container_id: str) -> Dict[str, Any]:
        """Analyze repository structure to identify languages, frameworks, and structure.
        This method relies entirely on Claude's intelligence to analyze the repository.
        
        Args:
            container_id: ID of the container with the cloned repo
            
        Returns:
            Dictionary with repository analysis information
        """
        try:
            container = self.docker_client.containers.get(container_id)
            
            # Ask Claude to generate a shell script to analyze the repository structure
            system_prompt = """
            You are an expert in repository analysis and language detection. Your task is to create a shell script that 
            will thoroughly analyze a Git repository's structure and detect programming languages, frameworks, and other 
            important characteristics.
            
            Return ONLY the shell script without any additional text, explanations, or formatting.
            The script should be compatible with a Python container running Alpine Linux.
            
            The script should gather comprehensive information about:
            1. All programming languages used in the repository (with detection based on file extensions and content)
            2. Frameworks and libraries used (based on import statements, configuration files, etc.)
            3. Package management files and dependency information
            4. Test directories and test files
            5. Project structure and organization
            
            The script should output its findings in JSON format to stdout.
            """
            
            prompt = """
            Create a shell script to analyze the repository at /workspace/repo. The script should:
            
            1. Detect all programming languages used in the repository
            2. Identify frameworks and libraries
            3. Find package management files (requirements.txt, setup.py, pyproject.toml, package.json, etc.)
            4. Locate test directories and files
            5. Analyze the overall project structure
            
            The script should output a JSON object with these keys:
            - languages: array of strings (programming languages detected)
            - frameworks: array of strings (frameworks detected)
            - package_files: array of file paths (dependency files found)
            - test_directories: array of directory paths
            - test_files: array of file paths
            - structure_summary: string (brief overview of project structure)
            
            Make the script as thorough as possible, using multiple detection methods for each category.
            """
            
            # Get Claude's shell script for repository analysis
            analysis_script = self.ask_claude(prompt, system_prompt)
            
            # Clean up the script (remove markdown code blocks if present)
            if "```" in analysis_script:
                parts = analysis_script.split("```")
                if len(parts) >= 3:
                    # Extract the code between the first pair of ``` markers
                    analysis_script = parts[1]
                    # Remove language identifier if present (e.g., ```bash)
                    if analysis_script.split("\n", 1)[0].strip() in ["sh", "bash", "shell"]:
                        analysis_script = analysis_script.split("\n", 1)[1]
            
            analysis_script = analysis_script.strip()
            
            # Ensure the script starts with a proper shebang
            if not analysis_script.startswith("#!/"):
                analysis_script = "#!/bin/sh\n" + analysis_script
            
            # Write the script to a temporary file in the container
            script_path = "/tmp/analyze_repo.sh"
            container.exec_run(["sh", "-c", f"echo '{analysis_script}' > {script_path}"])
            container.exec_run(["chmod", "+x", script_path])
            
            # Execute the analysis script
            logger.info("Executing repository analysis script...")
            exit_code, output = container.exec_run(["sh", "-c", f"cd /workspace/repo && {script_path}"])
            
            if exit_code != 0:
                logger.info(f"Analysis script failed with exit code {exit_code}")
                logger.info(f"Error output: {output.decode('utf-8', errors='replace')}")
                
                # Ask Claude to fix the script based on the error
                fix_prompt = f"""
                The repository analysis script failed with the following error:
                
                ```
                {output.decode('utf-8', errors='replace')}
                ```
                
                Please create a simpler, more robust script that will work in an Alpine Linux container.
                Focus on basic file system operations that are guaranteed to work.
                """
                
                # Get Claude's fixed script
                analysis_script = self.ask_claude(fix_prompt, system_prompt)
                
                # Clean up the fixed script
                if "```" in analysis_script:
                    parts = analysis_script.split("```")
                    if len(parts) >= 3:
                        analysis_script = parts[1]
                        if analysis_script.split("\n", 1)[0].strip() in ["sh", "bash", "shell"]:
                            analysis_script = analysis_script.split("\n", 1)[1]
                
                analysis_script = analysis_script.strip()
                if not analysis_script.startswith("#!/"):
                    analysis_script = "#!/bin/sh\n" + analysis_script
                
                # Try again with the fixed script
                container.exec_run(["sh", "-c", f"echo '{analysis_script}' > {script_path}"])
                container.exec_run(["chmod", "+x", script_path])
                exit_code, output = container.exec_run(["sh", "-c", f"cd /workspace/repo && {script_path}"])
            
            # Parse the JSON output from the script
            try:
                analysis_json = output.decode('utf-8', errors='replace')
                
                # Extract just the JSON part from the response
                json_start = analysis_json.find('{')
                json_end = analysis_json.rfind('}')
                if json_start >= 0 and json_end >= 0:
                    analysis_json = analysis_json[json_start:json_end+1]
                
                analysis = json.loads(analysis_json)
                logger.info(f"Repository analysis complete: {len(analysis.get('languages', []))} languages detected")
                return analysis
            except json.JSONDecodeError as e:
                logger.info(f"Error parsing repository analysis: {str(e)}")
                logger.info(f"Raw analysis output: {output.decode('utf-8', errors='replace')}")
                
                # Ask Claude to interpret the raw output and structure it as JSON
                interpret_prompt = f"""
                The repository analysis script produced the following output, which is not valid JSON:
                
                ```
                {output.decode('utf-8', errors='replace')}
                ```
                
                Based on this output, please create a properly structured JSON object with these keys:
                - languages: array of strings (programming languages detected)
                - frameworks: array of strings (frameworks detected)
                - package_files: array of file paths (dependency files found)
                - test_directories: array of directory paths
                - test_files: array of file paths
                - structure_summary: string (brief overview of project structure)
                
                Return ONLY the JSON object without any additional text or explanations.
                """
                
                # Get Claude's interpretation
                analysis_json = self.ask_claude(interpret_prompt)
                
                try:
                    # Extract just the JSON part from the response
                    json_start = analysis_json.find('{')
                    json_end = analysis_json.rfind('}')
                    if json_start >= 0 and json_end >= 0:
                        analysis_json = analysis_json[json_start:json_end+1]
                    
                    analysis = json.loads(analysis_json)
                    logger.info(f"Repository analysis complete (via Claude interpretation): {len(analysis.get('languages', []))} languages detected")
                    return analysis
                except json.JSONDecodeError:
                    logger.info("Failed to parse Claude's interpretation as JSON")
                    # Return a basic structure if parsing fails
            
            analysis_json = self.ask_claude(prompt)
            
            try:
                # Extract just the JSON part from the response
                json_start = analysis_json.find('{')
                json_end = analysis_json.rfind('}')
                if json_start >= 0 and json_end >= 0:
                    analysis_json = analysis_json[json_start:json_end+1]
                
                analysis = json.loads(analysis_json)
                logger.info(f"Repository analysis complete: {len(analysis.get('languages', []))} languages detected")
                return analysis
            except json.JSONDecodeError as e:
                logger.info(f"Error parsing repository analysis: {str(e)}")
                logger.info(f"Raw analysis: {analysis_json}")
                # Return a basic structure if parsing fails
                return {
                    "languages": [],
                    "frameworks": [],
                    "package_files": [],
                    "test_directories": [],
                    "structure_summary": "Analysis failed"
                }
                
        except Exception as e:
            logger.info(f"Error analyzing repository: {str(e)}")
            return {
                "languages": [],
                "frameworks": [],
                "package_files": [],
                "test_directories": [],
                "structure_summary": f"Error: {str(e)}"
            }
    
    def generate_dependency_commands(self, repo_analysis: Dict[str, Any]) -> str:
        """Generate a robust shell script to install project dependencies.

        This method asks Claude to create an *Alpine-compatible* installation
        script.  It pre-processes the ``repo_analysis`` so that Claude always
        receives reasonable defaults (e.g. Python + requirements.txt) even if
        static analysis failed to detect languages.  The response is cleaned so
        that ONLY the raw shell script text is returned – no markdown fences or
        language hints.
        """
        # ------------------------------------------------------------------
        # 1.  Sanity-check / massage repo_analysis so Claude receives signal.
        # ------------------------------------------------------------------
        if not repo_analysis or not repo_analysis.get("languages"):
            # If detection failed, assume a typical Python project with a
            # requirements.txt file present so that the installation script is
            # still useful instead of empty.
            repo_analysis = repo_analysis.copy() if repo_analysis else {}
            repo_analysis.update({
                "languages": ["python"],
                "has_requirements_txt": True,
            })
            logger.info("No languages detected – defaulting repo_analysis to Python project")

        # ------------------------------------------------------------------
        # 2.  Compose prompt + system instructions
        # ------------------------------------------------------------------
        system_prompt = (
            "You are an expert DevOps engineer for Alpine Linux containers. "
            "Generate a POSIX-sh installation script *only* (no markdown fences). "
            "Constraints:\n"
            "• Start with #!/bin/sh and set -e\n"
            "• Use apk for system packages, pip for Python\n"
            "• Never use heredoc syntax or back-ticks – only $(…)\n"
            "• Always install python3-dev gcc musl-dev build-base for wheels\n"
            "• Always install pytest and pytest-cov for test execution\n"
            "• Add clear log echo statements so users can trace progress\n"
        )

        user_prompt = (
            "Create a shell script that installs every dependency required to run "
            "the repository tests based on the following analysis.  Return the "
            "script text ONLY.  Do NOT wrap in ``` fences.\n\n"
            f"Repository analysis JSON:\n{json.dumps(repo_analysis, indent=2)}"
        )

        # ------------------------------------------------------------------
        # 3.  Ask Claude for the script
        # ------------------------------------------------------------------
        script_text = self.ask_claude(user_prompt, system_prompt)
        if not script_text:
            return ""

        # ------------------------------------------------------------------
        # 4.  Strip possible markdown / fencing returned by Claude
        # ------------------------------------------------------------------
        if "```" in script_text:
            parts = script_text.split("```")
            if len(parts) >= 3:
                script_text = parts[1]
                if script_text.startswith(("sh", "bash")):
                    script_text = script_text.split("\n", 1)[1]
        script_text = script_text.strip()

        # Ensure mandatory header and error handling
        if not script_text.startswith("#!/"):
            script_text = "#!/bin/sh\nset -e\n" + script_text
        else:
            lines = script_text.split("\n")
            if len(lines) < 2 or "set -e" not in lines[1]:
                script_text = "\n".join([lines[0], "set -e"] + lines[1:])

        logger.info(f"Generated dependency installation commands:\n{script_text[:200]}…")
        return script_text
        """
        if not script_text:
        - The script will run in an Alpine Linux container (python:3.13-alpine)
        - You MUST use Alpine package manager (apk) instead of apt-get/apt
        - Use 'sh' shebang (#!/bin/sh) not bash, as Alpine uses BusyBox sh
        - For Python packages requiring compilation, install 'python3-dev', 'gcc', and 'musl-dev'
        - For C/C++ dependencies, use 'build-base' package
        - For JavaScript, install 'nodejs' and 'npm' packages
        
        YOUR SCRIPT MUST INCLUDE:
        1. Robust error handling with clear error messages
        2. Verification steps to confirm successful installation
        3. Appropriate package installation based on detected languages and frameworks
        4. Installation of project dependencies from package files (requirements.txt, package.json, etc.)
        5. Installation of the package in development mode if applicable
        
        BE CREATIVE AND THOROUGH in addressing potential issues before they arise.
        """
        

    
    def install_dependencies(self, container_id: str, repo_analysis: Optional[Dict[str, Any]] = None) -> bool:
        """Install dependencies required to run tests using Claude's intelligence.
        
        This method relies entirely on Claude to analyze the repository and generate
        a robust dependency installation script. No hardcoded patterns or assumptions.
        
        Args:
            container_id: ID of the container with the cloned repo
            repo_analysis: Optional repository analysis (can be None, Claude will handle detection)
            
        Returns:
            True if dependency installation succeeded, False otherwise
        """
        try:
            container = self.docker_client.containers.get(container_id)
            
            # Ask Claude to generate a dependency installation script directly
            system_prompt = """
            You are an expert DevOps engineer specializing in Python environments. Your task is to create a shell script that 
            will install all dependencies required to run tests for a Python repository.
            
            Return ONLY the shell script without any additional text, explanations, or formatting.
            The script will run in a Python Alpine Linux container (python:3.13-alpine).
            
            Your script MUST:
            1. Use Alpine package manager (apk) for system packages
            2. Use pip for Python packages
            3. Start with proper shebang and error handling (set -e)
            4. Install common build dependencies (python3-dev, gcc, musl-dev, build-base)
            5. Detect and install dependencies from standard Python files (requirements.txt, setup.py, pyproject.toml)
            6. Install the package in development mode if applicable
            7. Install pytest and other testing frameworks
            8. Include clear logging for each step
            9. Verify successful installation
            
            BE THOROUGH and handle edge cases. The script should work without user intervention.
            """
            
            # Get basic file listings for context
            exit_code, ls_output = container.exec_run("cd /workspace/repo && ls -la")
            exit_code, find_output = container.exec_run(
                "cd /workspace/repo && find . -type f -name '*.py' -o -name 'requirements.txt' -o -name 'setup.py' -o -name 'pyproject.toml' | sort | head -20"
            )
            
            # Check for common dependency files
            exit_code, req_check = container.exec_run("cd /workspace/repo && cat requirements.txt 2>/dev/null || echo 'Not found'")
            exit_code, setup_check = container.exec_run("cd /workspace/repo && cat setup.py 2>/dev/null || echo 'Not found'")
            exit_code, pyproject_check = container.exec_run("cd /workspace/repo && cat pyproject.toml 2>/dev/null || echo 'Not found'")
            
            # Prepare prompt with repository context
            prompt = f"""
            Create a shell script to install all dependencies for the Python repository at /workspace/repo.
            
            Repository structure:
            ```
            {ls_output.decode('utf-8', errors='replace')}
            ```
            
            Python and dependency files:
            ```
            {find_output.decode('utf-8', errors='replace')}
            ```
            
            Requirements.txt (if exists):
            ```
            {req_check.decode('utf-8', errors='replace')[:500]}
            ```
            
            Setup.py (if exists):
            ```
            {setup_check.decode('utf-8', errors='replace')[:500]}
            ```
            
            Pyproject.toml (if exists):
            ```
            {pyproject_check.decode('utf-8', errors='replace')[:500]}
            ```
            
            Create a robust installation script that will:
            1. Install all system dependencies needed
            2. Install all Python dependencies
            3. Install the package itself in development mode if applicable
            4. Install testing frameworks and dependencies
            
            The script should be thorough and handle edge cases automatically.
            """
            
            # Get Claude's dependency installation script
            dependency_commands = self.ask_claude(prompt, system_prompt)
            
            # Clean up the script (remove markdown code blocks if present)
            if "```" in dependency_commands:
                parts = dependency_commands.split("```")
                if len(parts) >= 3:
                    # Extract the code between the first pair of ``` markers
                    dependency_commands = parts[1]
                    # Remove language identifier if present (e.g., ```bash)
                    if dependency_commands.split("\n", 1)[0].strip() in ["sh", "bash", "shell"]:
                        dependency_commands = dependency_commands.split("\n", 1)[1]
            
            dependency_commands = dependency_commands.strip()
            
            # Ensure the script starts with a proper shebang
            if not dependency_commands.startswith("#!/"):
                dependency_commands = "#!/bin/sh\nset -e\n\n" + dependency_commands
            
            # Ensure the script has proper error handling
            if "set -e" not in dependency_commands:
                dependency_commands = dependency_commands.replace("#!/bin/sh", "#!/bin/sh\nset -e")
            
            logger.info("Generated dependency installation commands:")
            logger.info(dependency_commands[:500] + "..." if len(dependency_commands) > 500 else dependency_commands)
            
            # Write commands to a script file using a more robust approach
            script_path = "/tmp/install_dependencies.sh"
            
            # Create the script file line by line to avoid heredoc issues
            container.exec_run(["sh", "-c", f"echo '' > {script_path}"])
            
            # Split the script into lines and write each line separately
            for line in dependency_commands.split('\n'):
                # Escape any single quotes in the line
                escaped_line = line.replace("'", "'\"'\"'")
                container.exec_run(["sh", "-c", f"echo '{escaped_line}' >> {script_path}"])
            
            # Make the script executable
            container.exec_run(["chmod", "+x", script_path])
            
            # Execute dependency installation with retries
            for attempt in range(1, self.max_retries + 1):
                logger.info(f"Installing dependencies (attempt {attempt}/{self.max_retries})...")
                exit_code, output = container.exec_run(
                    ["sh", "-c", f"cd /workspace/repo && {script_path}"],
                    environment={
                        "PYTHONPATH": "/workspace/repo",
                        "PYTHONDONTWRITEBYTECODE": "1",  # Don't create .pyc files
                        "PYTHONUNBUFFERED": "1"  # Unbuffered output
                    }
                )
                
                if exit_code == 0:
                    logger.info("Dependencies installed successfully")
                    return True
                else:
                    logger.info(f"Dependency installation failed with exit code {exit_code}")
                    error_output = output.decode('utf-8', errors='replace')
                    logger.info(f"Error output: {error_output[:500]}..." if len(error_output) > 500 else error_output)
                    
                    # Try to fix installation issues if not the last attempt
                    if attempt < self.max_retries:
                        fixed_commands = self.fix_dependency_issues(container_id, dependency_commands, error_output)
                        if fixed_commands and fixed_commands != dependency_commands:
                            dependency_commands = fixed_commands
                            logger.info("Generated fixed dependency installation commands:")
                            logger.info(dependency_commands[:500] + "..." if len(dependency_commands) > 500 else dependency_commands)
                            
                            # Write the fixed commands using the same robust approach
                            container.exec_run(["sh", "-c", f"echo '' > {script_path}"])
                            for line in dependency_commands.split('\n'):
                                escaped_line = line.replace("'", "'\"'\"'")
                                container.exec_run(["sh", "-c", f"echo '{escaped_line}' >> {script_path}"])
                            container.exec_run(["chmod", "+x", script_path])
                        else:
                            logger.info("Could not fix dependency issues, trying again...")
            
            return False
                
        except Exception as e:
            logger.info(f"Error installing dependencies: {str(e)}")
            return False
    
    def fix_dependency_issues(self, container_id: str, original_commands: str, error_output: str) -> str:
        """Fix dependency installation issues using Claude.
        
        Args:
            container_id: ID of the container with the cloned repo
            original_commands: Original installation commands
            error_output: Error output from failed installation
            
        Returns:
            Fixed dependency installation commands or empty string if fixing failed
        """
        try:
            # Get container OS information to provide context
            container = self.docker_client.containers.get(container_id)
            exit_code, output = container.exec_run("cat /etc/os-release")
            os_info = output.decode('utf-8', errors='replace')
            
            # Create system prompt for Alpine Linux
            system_prompt = """
            You are an expert DevOps engineer specializing in fixing dependency issues in Alpine Linux containers.
            Your task is to debug and fix shell script errors for installing dependencies.
            
            IMPORTANT TECHNICAL DETAILS:
            - The script runs in an Alpine Linux container (python:3.13-alpine)
            - You MUST use Alpine package manager (apk) instead of apt-get/apt
            - Use 'sh' syntax, not bash-specific features, as Alpine uses BusyBox sh
            - For Python packages requiring compilation, ensure 'python3-dev', 'gcc', and 'musl-dev' are installed
            - For C/C++ dependencies, use 'build-base' package
            
            YOUR FIXED SCRIPT MUST:
            1. Address the specific errors in the error output
            2. Include robust error handling
            3. Use Alpine-compatible commands and syntax
            4. Fix package names to match those available in Alpine
            
            BE THOROUGH in addressing all issues in the script.
            """
            
            # Prepare prompt for Claude to fix installation issues
            prompt = f"""
            The dependency installation commands encountered errors in an Alpine Linux container.
            
            Container OS information:
            ```
            {os_info}
            ```
            
            Original commands:
            ```sh
            {original_commands}
            ```
            
            Error output (last 2000 chars):
            ```
            {error_output[-2000:] if len(error_output) > 2000 else error_output}
            ```
            
            Please fix the issues in the commands and provide a corrected version that works in Alpine Linux.
            Remember to use 'apk' package manager, not apt-get or apt.
            Use Alpine-specific package names and ensure proper error handling.
            
            Return ONLY a shell script with the fixed commands.
            Do not include any explanations, only the commands to run.
            """
            
            fixed_commands = self.ask_claude(prompt, system_prompt)
            
            # Clean up the response to extract just the script
            if "```bash" in fixed_commands:
                fixed_commands = fixed_commands.split("```bash", 1)[1]
            elif "```sh" in fixed_commands:
                fixed_commands = fixed_commands.split("```sh", 1)[1]
            elif "```" in fixed_commands:
                fixed_commands = fixed_commands.split("```", 1)[1]
                
            if "```" in fixed_commands:
                fixed_commands = fixed_commands.split("```", 1)[0]
            
            fixed_commands = fixed_commands.strip()
            logger.info(f"Generated fixed dependency installation commands:\n{fixed_commands[:200]}...")
            return fixed_commands
                
        except Exception as e:
            logger.info(f"Error fixing dependency issues: {str(e)}")
            return ""
    
    def find_test_files(self, container_id: str, repo_analysis: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
        """Find integration test files in the repository using Claude's intelligence.
        
        This method relies entirely on Claude to discover test files in the repository,
        without any hardcoded patterns or assumptions. Claude will analyze the repository
        structure and identify appropriate test files based on naming conventions,
        imports, and other indicators.
        
        Args:
            container_id: ID of the container with the cloned repo
            repo_analysis: Optional repository analysis (can be None, Claude will handle detection)
            
        Returns:
            List of test files with metadata
        """
        try:
            container = self.docker_client.containers.get(container_id)
            
            # Get a high-level directory listing to provide context to Claude
            exit_code, ls_output = container.exec_run(["sh", "-c", "find /workspace/repo -type d -not -path '*/\.*' | sort"])
            directory_structure = ls_output.decode('utf-8', errors='replace') if exit_code == 0 else ""
            
            # Get a list of Python files to provide context to Claude
            exit_code, py_files = container.exec_run(["sh", "-c", "find /workspace/repo -type f -name '*.py' | head -n 30"])
            python_files = py_files.decode('utf-8', errors='replace') if exit_code == 0 else ""
            
            # Ask Claude to identify test files
            system_prompt = """
            You are an expert Python test engineer. Your task is to identify integration test files in a Python repository.
            
            Integration tests typically:
            1. Test interactions between multiple components or modules
            2. May be in directories named 'integration', 'tests', 'test', 'e2e', etc.
            3. Often have 'test', 'integration', or 'e2e' in their filenames
            4. Import testing frameworks like pytest, unittest, etc.
            
            DO NOT use any predefined patterns or rules. Analyze the repository structure and make your best judgment.
            
            Return your response as a JSON array of file paths, with a maximum of 3 test files. Focus on finding the most
            representative integration tests. If no clear integration tests exist, select the most comprehensive test files.
            
            Example response format:
            ```json
            [
              "/workspace/repo/tests/integration/test_api.py",
              "/workspace/repo/tests/test_integration.py"
            ]
            ```
            
            If you're not confident about a file being a test, include it anyway and explain your reasoning.
            """
            
            prompt = f"""Please identify the most relevant integration test files in this Python repository.
            
            Repository directory structure:
            {directory_structure}
            
            Sample of Python files:
            {python_files}
            
            Identify up to 3 files that are most likely to be integration tests. Return the full paths as a JSON array.
            """
            
            # Get Claude's response
            response = self.ask_claude(prompt, system_prompt)
            
            # Parse the JSON response
            test_file_paths = []
            try:
                # Extract JSON array from response if it's wrapped in markdown code blocks
                if "```json" in response and "```" in response.split("```json", 1)[1]:
                    json_str = response.split("```json", 1)[1].split("```", 1)[0].strip()
                    test_file_paths = json.loads(json_str)
                elif "```" in response and "```" in response.split("```", 1)[1]:
                    json_str = response.split("```", 1)[1].split("```", 1)[0].strip()
                    test_file_paths = json.loads(json_str)
                else:
                    # Try to find a JSON array directly in the response
                    match = re.search(r'\[\s*".*?".*?\]', response, re.DOTALL)
                    if match:
                        json_str = match.group(0)
                        test_file_paths = json.loads(json_str)
            except Exception as e:
                logger.info(f"Error parsing Claude's response: {str(e)}")
                logger.info(f"Raw response: {response}")
                
            # If parsing failed or no files found, ask Claude again with a simpler prompt
            if not test_file_paths:
                prompt = """The JSON parsing failed. Please provide a simple list of file paths, one per line, 
                without any JSON formatting or code blocks. Just the raw file paths of up to 3 test files."""
                response = self.ask_claude(prompt)
                test_file_paths = [line.strip() for line in response.split('\n') if line.strip() and line.strip().startswith('/workspace/repo/')]
            
            # Read the content of each file
            test_files = []
            for file_path in test_file_paths[:3]:  # Limit to first 3 files
                if file_path and file_path.startswith('/workspace/repo/'):
                    exit_code, content = container.exec_run(["cat", file_path])
                    if exit_code == 0:
                        test_files.append({
                            "path": file_path,
                            "content": content.decode('utf-8', errors='replace')
                        })
            
            logger.info(f"Found {len(test_files)} test files using Claude")
            return test_files
            
        except Exception as e:
            logger.info(f"Error finding test files: {str(e)}")
            return []
    
    def _format_test_files_for_prompt(self, test_files: List[Dict[str, str]]) -> str:
        """Format test files for inclusion in prompts.
        
        Args:
            test_files: List of dictionaries with test file paths and content
            
        Returns:
            Formatted string with test file information
        """
        result = ""
        for i, file in enumerate(test_files):
            result += f"File {i+1}: {file['path']}\n"
            result += f"Content:\n{file['content']}\n\n"
        return result
    
    def run_tests(self, container_id: str, test_files: List[Dict[str, str]]) -> Dict[str, Any]:
        """Run integration tests in the container using Claude's intelligence.
        
        Args:
            container_id: ID of the container with the cloned repo
            test_files: List of test files to run
            
        Returns:
            Dictionary with raw test results 
        """
        try:
            if not test_files:
                logger.info("No integration test files found, aborting test workflow")
                return {"success": False, "error": "No integration test files found"}
            
            container = self.docker_client.containers.get(container_id)
            
            # Collect test file paths
            test_file_paths = [file['path'] for file in test_files]
            
            # Compose prompt to ask Claude for a BusyBox sh-compatible shell script
            system_prompt = """
            You are an expert in shell scripting for Alpine Linux environments. 
            Your task is to create a BusyBox sh-compatible shell script that can run Python tests.
            Return ONLY the shell script with no markdown formatting or explanations.
            """
            
            prompt = f"""
            Create a BusyBox sh-compatible shell script to run Python tests in an Alpine Linux container (python:3.13-alpine).
            
            The script needs to run these specific test files:
            {json.dumps(test_file_paths, indent=2)}
            
            Requirements:
            1. The script must be compatible with BusyBox sh in Alpine Linux (NOT bash)
            2. Change to the repository directory (/workspace/repo)
            3. Set PYTHONPATH=/workspace/repo
            4. Run EACH test file individually with the appropriate test framework (detect if it's pytest or unittest)
            5. Handle errors gracefully and continue to the next test file if one fails
            6. Count and report the number of passed and failed tests
            7. Return exit code 1 if any tests fail, 0 if all pass
            
            Important compatibility notes:
            - Avoid bash-specific features like arrays
            - Use simple sh-compatible loops and conditionals
            - Properly quote all variables and paths
            - Initialize all variables before use
            - Use python3 command (not python)
            
            Return ONLY the shell script with no markdown formatting or explanations.
            """
            
            # Get the shell script from Claude
            test_script = self.ask_claude(prompt, system_prompt)
            
            # Clean up the script - remove markdown formatting if present
            if test_script.startswith("```") and "```" in test_script[3:]:
                test_script = test_script.split("```", 2)[1]
                if test_script.startswith("sh") or test_script.startswith("bash"):
                    test_script = test_script[test_script.find("\n")+1:]
                test_script = test_script.strip()
            
            # Ensure script starts with shebang
            if not test_script.startswith("#!/bin/sh"):
                test_script = "#!/bin/sh\n" + test_script
                
            # Fix common BusyBox sh compatibility issues
            # Replace bash arrays with sh-compatible alternatives
            test_script = re.sub(r'declare -a ([A-Za-z_][A-Za-z0-9_]*)', r'# No arrays in sh', test_script)
            test_script = re.sub(r'([A-Za-z_][A-Za-z0-9_]*)\[\]', r'# No arrays in sh', test_script)
            test_script = re.sub(r'([A-Za-z_][A-Za-z0-9_]*)\[([0-9]+)\]', r'\1_\2', test_script)
            
            # Replace [[ ]] with [ ]
            test_script = re.sub(r'\[\[ (.*?) \]\]', r'[ \1 ]', test_script)
            
            # Replace arithmetic expansion
            test_script = re.sub(r'\(\( (.*?) \)\)', r'$(expr \1)', test_script)
            
            # Replace string concatenation
            test_script = re.sub(r'([A-Za-z_][A-Za-z0-9_]*)=\$\1\+\+', r'\1=$(expr $\1 + 1)', test_script)
            
            # Ensure proper quoting
            test_script = re.sub(r'echo (.*?)$', r'echo "\1"', test_script)
            
            # Write the script to the container
            script_path = "/workspace/scripts/run_tests.sh"
            container.exec_run(["mkdir", "-p", "/workspace/scripts"])
            container.exec_run(["sh", "-c", f"cat > {script_path} << 'EOL'\n{test_script}\nEOL"])
            container.exec_run(["chmod", "+x", script_path])
            
            # Execute the script
            environment = {
                "PYTHONPATH": "/workspace/repo",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1"
            }
            
            exit_code, output = container.exec_run(
                ["sh", "-c", script_path],
                environment=environment,
                workdir="/workspace/repo"
            )
            
            # Process output
            stdout = output.decode('utf-8', errors='replace')
            stderr = ""  # In this implementation, stderr is combined with stdout
            
            # Determine success based on exit code
            success = exit_code == 0
            
            # Return raw test results - the run method will handle formatting according to the schema
            return {
                "success": success,
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": exit_code,
                "test_files": test_files  # Return the full test files with content, not just paths
            }
            
        except Exception as e:
            logger.info(f"Error in run_tests: {str(e)}")
            return {"success": False, "error": str(e)}

    
    def run(self, container_id: str, repo_url: str) -> Dict[str, Any]:
        """Run the full test workflow for a repository using Claude's intelligence at every step.
        
        Args:
            container_id: ID of the container with the cloned repo
            repo_url: URL of the repository
            
        Returns:
            Dictionary with test results formatted according to the specified JSON schema
        """
        try:
            logger.info(f"Starting test workflow for {repo_url}")
            
            # Install Claude SDK in container
            if not self.install_claude_code(container_id):
                return self._format_error_result(repo_url, "Failed to install Claude SDK", "unknown")
            
            # Get repository information using Claude
            container = self.docker_client.containers.get(container_id)
            
            # Ask Claude to get the commit ID
            system_prompt = """
            You are an expert Git user. Your task is to retrieve the current commit ID (SHA) of a Git repository.
            Return ONLY the full commit SHA without any additional text, explanations, or formatting.
            """
            
            prompt = """Please retrieve the current commit ID (SHA) of the Git repository located at /workspace/repo.
            Execute the appropriate Git command and return only the full commit SHA.
            """
            
            commit_id = self.ask_claude(prompt, system_prompt).strip()
            
            # Clean up the commit ID - remove any non-hex characters
            commit_id = ''.join(c for c in commit_id if c in '0123456789abcdefABCDEF')
            if not commit_id or len(commit_id) < 7:
                # Fallback to direct command if Claude's response isn't a valid SHA
                exit_code, commit_output = container.exec_run(["sh", "-c", "cd /workspace/repo && git rev-parse HEAD"])
                commit_id = commit_output.decode('utf-8', errors='replace').strip() if exit_code == 0 else "unknown"
            
            # Install dependencies using Claude (no need for repo analysis, Claude will handle it)
            if not self.install_dependencies(container_id, None):
                return self._format_error_result(repo_url, "Failed to install dependencies", commit_id)
            
            # Find integration test files using Claude (no need for repo analysis, Claude will handle it)
            test_files = self.find_test_files(container_id, None)
            if not test_files:
                return self._format_error_result(repo_url, "No integration test files found", commit_id)
            
            # Run tests using Claude - this captures stdout/stderr separately
            test_result = self.run_tests(container_id, test_files)
            
            # Ask Claude to format the results according to the specified JSON schema
            system_prompt = """
            You are an expert in JSON data formatting. Your task is to format test results according to a specific schema.
            
            The required JSON schema is:
            {
              "Repo": {
                "remoteUrl": "string",
                "languages": ["py"]
              },
              "IntegrationTest": {
                "fileContent": "string"
              },
              "IntegrationTestRun": {
                "commitId": "string",
                "result": {
                  "stdout": "string",
                  "stderr": "string",
                  "returnCode": number
                },
                "pass": boolean
              }
            }
            
            Return ONLY the formatted JSON without any additional text or explanations.
            """
            
            # Prepare the test data for Claude
            test_file_content = test_files[0].get("content", "") if test_files else ""
            test_file_content_preview = test_file_content[:1000] + "..." if len(test_file_content) > 1000 else test_file_content
            
            stdout = test_result.get("stdout", "")
            stderr = test_result.get("stderr", "")
            exit_code = test_result.get("exit_code", 1)
            success = test_result.get("success", False)
            
            prompt = f"""Format the following test results according to the specified JSON schema:
            
            Repository URL: {repo_url}
            Languages: Python only ("py")
            Commit ID: {commit_id}
            
            Integration Test File Content (preview):
            {test_file_content_preview}
            
            Test Results:
            - Success: {success}
            - Exit Code: {exit_code}
            - Standard Output: {stdout[:500]}... (truncated)
            - Standard Error: {stderr[:500]}... (truncated)
            
            Please format this data according to the schema in your system prompt.
            """
            
            # Get Claude's formatted JSON response
            formatted_json_str = self.ask_claude(prompt, system_prompt)
            
            # Parse the JSON response
            try:
                # Extract JSON if it's wrapped in markdown code blocks
                if "```json" in formatted_json_str and "```" in formatted_json_str.split("```json", 1)[1]:
                    json_str = formatted_json_str.split("```json", 1)[1].split("```", 1)[0].strip()
                    result = json.loads(json_str)
                elif "```" in formatted_json_str and "```" in formatted_json_str.split("```", 1)[1]:
                    json_str = formatted_json_str.split("```", 1)[1].split("```", 1)[0].strip()
                    result = json.loads(json_str)
                else:
                    # Try to parse the entire response as JSON
                    result = json.loads(formatted_json_str)
            except Exception as e:
                logger.info(f"Error parsing Claude's JSON response: {str(e)}")
                # Fall back to direct formatting
                result = {
                    "Repo": {
                        "remoteUrl": repo_url,
                        "languages": ["py"]
                    },
                    "IntegrationTest": {
                        "fileContent": test_file_content
                    },
                    "IntegrationTestRun": {
                        "commitId": commit_id,
                        "result": {
                            "stdout": stdout,
                            "stderr": stderr,
                            "returnCode": exit_code
                        },
                        "pass": success
                    }
                }
            
            # Ensure the result has the correct structure
            if not isinstance(result, dict) or not all(k in result for k in ["Repo", "IntegrationTest", "IntegrationTestRun"]):
                # Fall back to direct formatting if structure is incorrect
                result = {
                    "Repo": {
                        "remoteUrl": repo_url,
                        "languages": ["py"]
                    },
                    "IntegrationTest": {
                        "fileContent": test_file_content
                    },
                    "IntegrationTestRun": {
                        "commitId": commit_id,
                        "result": {
                            "stdout": stdout,
                            "stderr": stderr,
                            "returnCode": exit_code
                        },
                        "pass": success
                    }
                }
            
            # Make sure the full test file content is included (Claude might truncate it)
            result["IntegrationTest"]["fileContent"] = test_file_content
            
            # Make sure the full stdout/stderr are included (Claude might truncate them)
            result["IntegrationTestRun"]["result"]["stdout"] = stdout
            result["IntegrationTestRun"]["result"]["stderr"] = stderr
            
            return result
            
        except Exception as e:
            logger.info(f"Error in test workflow: {str(e)}")
            return self._format_error_result(repo_url, str(e), "unknown")
            
    def _format_error_result(self, repo_url: str, error_message: str, commit_id: str) -> Dict[str, Any]:
        """Format an error result according to the specified JSON schema.
        
        Args:
            repo_url: URL of the repository
            error_message: Error message to include
            commit_id: Commit ID of the repository
            
        Returns:
            Dictionary with error information formatted according to the schema
        """
        return {
            "Repo": {
                "remoteUrl": repo_url,
                "languages": ["py"]
            },
            "IntegrationTest": {
                "fileContent": ""
            },
            "IntegrationTestRun": {
                "commitId": commit_id,
                "result": {
                    "stdout": "",
                    "stderr": error_message,
                    "returnCode": 1
                },
                "pass": False
            }
        }

# Command-line interface
def main():
    parser = argparse.ArgumentParser(description="Run integration tests on a cloned repository")
    parser.add_argument("container_id", help="ID of the Docker container with the cloned repo")
    parser.add_argument("repo_url", help="URL of the repository")
    parser.add_argument("--db", default=DEFAULT_DB_NAME, help="Database name")
    args = parser.parse_args()
    
    agent = TestAgent(db_name=args.db)
    results = agent.run(args.container_id, args.repo_url)
    
    logger.info(json.dumps(results, indent=2))

if __name__ == "__main__":
    main()
