#!/usr/bin/env python3

"""
Extracts and formats test results using Claude

This agent processes raw test output, evaluates test validity,
extracts relevant test results, and stores them in a structured JSON format.
"""

import os
import json
import traceback
import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
import re

from anthropic import Anthropic
from dotenv import load_dotenv

# Load env vars
load_dotenv()

# Logger setup
logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

class ResultAgent:
    """
    Agent responsible for evaluating test validity and extracting test results using Claude.
    
    This agent takes raw test output, evaluates if tests are valid or failed due to
    environment/dependency issues, and extracts relevant test results while ignoring
    setup logs and installation messages. It can also suggest fixes for environment issues.
    """
    
    def __init__(self, output_dir: str = None):
        """
        Initialize the ResultAgent.
        
        Args:
            output_dir: Directory to store result files (defaults to current directory)
        """
        self.claude_api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY")
        if not self.claude_api_key:
            raise ValueError("ANTHROPIC_API_KEY or CLAUDE_API_KEY environment variable must be set")
            
        self.claude_client = Anthropic(api_key=self.claude_api_key)
        self.output_dir = output_dir or os.getcwd()
        
        # Create output directory if it doesn't exist
        os.makedirs(self.output_dir, exist_ok=True)
    
    def ask_claude(self, prompt: str, system_prompt: str = None) -> str:
        """
        Send a prompt to Claude API and get the response.
        
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
    
    def evaluate_test_validity(self, test_output: str) -> Dict[str, Any]:
        """
        Evaluate if tests are valid or failed due to environment/dependency issues.
        
        Args:
            test_output: Raw test output including setup logs and test results
            
        Returns:
            Dictionary with test validity evaluation and potential fixes
        """
        try:
            system_prompt = """
            You are an expert at analyzing Python test outputs and determining test validity.
            Your task is to evaluate whether test failures are due to actual code issues or due to 
            environment/dependency problems that prevent proper test execution.
            
            Categorize the test run into one of these categories:
            1. VALID_SUCCESS: Tests ran successfully and passed
            2. VALID_FAILURE: Tests ran successfully but failed due to actual code issues
            3. INVALID_ENVIRONMENT: Tests failed due to environment issues (missing dependencies, version conflicts, etc.)
            4. INVALID_SETUP: Tests couldn't run due to setup issues (syntax errors, import errors before tests run)
            5. INVALID_OTHER: Other issues preventing proper test execution
            
            For INVALID cases, suggest specific fixes that could be applied without changing the core code structure.
            These should be command-line fixes or environment adjustments that might resolve the issues.
            
            Format your response as a JSON structure with these fields:
            - validity: One of the categories above
            - reason: Brief explanation of your categorization
            - fixable: Boolean indicating if the issue can be fixed without code changes
            - suggested_fix: Command or action to fix the issue (only for fixable issues)
            - confidence: Number between 0-1 indicating your confidence in this assessment
            """
            
            prompt = f"""Please analyze the following test output and evaluate whether the tests are valid or failed due to environment/dependency issues.
            
            RAW TEST OUTPUT:
            ```
            {test_output}
            ```
            
            Respond with a JSON structure as described in the system prompt.
            """
            
            # Call Claude to evaluate test validity
            evaluation_response = self.ask_claude(prompt, system_prompt)
            
            # Try to parse the JSON response
            try:
                # Find JSON in the response (it might be surrounded by text)
                json_match = re.search(r'\{[\s\S]*\}', evaluation_response)
                if json_match:
                    evaluation_json = json.loads(json_match.group(0))
                else:
                    # Fallback if no JSON found
                    evaluation_json = {
                        "validity": "UNKNOWN",
                        "reason": "Could not parse evaluation response",
                        "fixable": False,
                        "confidence": 0
                    }
            except json.JSONDecodeError:
                # Fallback if JSON parsing fails
                evaluation_json = {
                    "validity": "UNKNOWN",
                    "reason": "Could not parse evaluation response",
                    "fixable": False,
                    "confidence": 0
                }
            
            # Add timestamp and raw response
            evaluation_json["timestamp"] = datetime.now().isoformat()
            evaluation_json["raw_evaluation"] = evaluation_response
            
            return evaluation_json
            
        except Exception as e:
            logger.info(f"Error evaluating test validity: {str(e)}")
            logger.info(f"Traceback: {traceback.format_exc()}")
            return {
                "validity": "ERROR",
                "reason": f"Error evaluating test validity: {str(e)}",
                "fixable": False,
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
    
    def extract_test_results(self, test_output: str) -> Dict[str, Any]:
        """
        Extract test results from raw output using Claude.
        
        Args:
            test_output: Raw test output including setup logs and test results
            
        Returns:
            Dictionary with parsed test results
        """
        try:
            system_prompt = """
            You are an expert at analyzing Python test outputs. Your task is to extract only the relevant test results 
            from the raw output of a test run. Ignore all setup logs, installation messages, and other non-test output.
            
            Focus on extracting:
            1. Test names and their pass/fail status
            2. Error messages and tracebacks for failed tests
            3. A summary of how many tests passed/failed
            4. Any critical warnings or errors that might explain test failures
            
            Your output MUST follow this exact JSON schema format:
            
            ```json
            {
              "Repo": {
                "remoteUrl": "string (e.g., 'https://github.com/org/project.git')",
                "languages": ["py"] // Python repos only, lowercase language code
              },
              "IntegrationTest": {
                "fileContent": "the content of the test file that we are running copied and paste here"
              },
              "IntegrationTestRun": {
                "commitId": "string (commit SHA for the current repo state)",
                "result": {
                  "stdout": "string (all standard output)", 
                  "stderr": "string (all error output)",
                  "returnCode": 0 // integer exit code
                },
                "pass": true/false // boolean indicating if tests passed
              }
            }
            ```
            
            Do not include any installation logs, pip commands, or system setup information.
            """
            
            prompt = f"""Please analyze the following test output and extract only the relevant test results, 
            error messages, and summary information. Ignore all setup logs, installation messages, and other non-test output.
            
            RAW TEST OUTPUT:
            ```
            {test_output}
            ```
            
            Please provide a clean, structured summary of just the test results.
            """
            
            # Call Claude to analyze the output
            analyzed_output = self.ask_claude(prompt, system_prompt)
            
            # Return the analyzed output
            return {
                "analyzed_output": analyzed_output,
                "raw_output": test_output,
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            logger.info(f"Error analyzing test results with Claude: {str(e)}")
            logger.info(f"Traceback: {traceback.format_exc()}")
            return {
                "analyzed_output": "", 
                "raw_output": test_output,
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
    
    def extract_and_save_results(self, test_output: str, repo_name: str = None) -> Dict[str, Any]:
        """
        Extract test results and save them to a JSON file.
        
        Args:
            test_output: Raw test output including setup logs and test results
            repo_name: Optional name of the repository being tested
            
        Returns:
            Dictionary with parsed test results and file path
        """
        try:
            # First evaluate test validity
            validity_results = self.evaluate_test_validity(test_output)
            
            # Extract test results
            results = self.extract_test_results(test_output)
            
            # Combine results
            combined_results = {
                **results,
                "validity": validity_results.get("validity", "UNKNOWN"),
                "validity_reason": validity_results.get("reason", ""),
                "fixable": validity_results.get("fixable", False),
                "suggested_fix": validity_results.get("suggested_fix", ""),
                "validity_confidence": validity_results.get("confidence", 0)
            }
            
            # Add repo name if provided
            if repo_name:
                combined_results["repo_name"] = repo_name
            
            # Generate filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            repo_prefix = f"{repo_name}_" if repo_name else ""
            filename = f"{repo_prefix}test_results_{timestamp}.json"
            filepath = os.path.join(self.output_dir, filename)
            
            # Save to JSON file
            with open(filepath, 'w') as f:
                json.dump(combined_results, f, indent=2)
            
            logger.info(f"Test results saved to {filepath}")
            
            # Add filepath to results
            combined_results["filepath"] = filepath
            return combined_results
        except Exception as e:
            logger.info(f"Error saving test results: {str(e)}")
            logger.info(f"Traceback: {traceback.format_exc()}")
            return {
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
    
    def extract_results_from_files(self, test_files: List[Dict[str, str]], test_output: str, 
                                  success: bool, exit_code: int, repo_name: str = None, commit_id: str = None) -> Dict[str, Any]:
        """
        Extract results from test files and raw output, format them, and save to JSON.
        
        This method is designed to be compatible with TestAgent's _format_test_results method.
        
        Args:
            test_files: List of test files that were run
            test_output: Raw test output
            success: Whether the tests passed overall
            exit_code: Exit code from the test run
            repo_name: Optional name of the repository being tested
            commit_id: Optional commit ID of the repository
            
        Returns:
            Dictionary with formatted test results including validity assessment
        """
        # First evaluate test validity
        validity_results = self.evaluate_test_validity(test_output)
        
        # Extract results using Claude
        results = self.extract_test_results(test_output)
        
        # Format results according to the specified JSON schema
        # Get the first test file content if available
        test_file_content = ""
        if test_files and len(test_files) > 0:
            test_file_content = test_files[0].get("content", "")
        
        # Extract stdout and stderr from test_output if not already separated
        stdout = test_output
        stderr = ""
        
        # Format according to the required schema
        formatted_results = {
            "Repo": {
                "remoteUrl": repo_name or "unknown",
                "languages": ["py"]  # Python repos only as specified
            },
            "IntegrationTest": {
                "fileContent": test_file_content
            },
            "IntegrationTestRun": {
                "commitId": commit_id or "unknown",
                "result": {
                    "stdout": stdout,
                    "stderr": stderr,
                    "returnCode": exit_code
                },
                "pass": success
            }
        }
        
        
        
        # Save to JSON file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        repo_prefix = f"{repo_name}_" if repo_name else ""
        commit_suffix = f"_{commit_id[:7]}" if commit_id else ""
        filename = f"{repo_prefix}test_results{commit_suffix}_{timestamp}.json"
        filepath = os.path.join(self.output_dir, filename)
        
        try:
            with open(filepath, 'w') as f:
                json.dump(formatted_results, f, indent=2)
            
            logger.info(f"Formatted test results saved to {filepath}")
            formatted_results["filepath"] = filepath
        except Exception as e:
            logger.info(f"Error saving formatted test results: {str(e)}")
            formatted_results["save_error"] = str(e)
        
        return formatted_results
