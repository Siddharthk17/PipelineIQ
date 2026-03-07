"""Exception hierarchy for PipelineIQ.

Every exception stores structured context data (not just a string message)
and provides to_dict() for API serialization. The hierarchy is designed
so callers can catch at the appropriate level of specificity.

Hierarchy:
    PipelineIQError (base)
    ├── PipelineConfigError
    │   ├── InvalidYAMLError
    │   ├── MissingRequiredFieldError
    │   ├── DuplicateStepNameError
    │   ├── InvalidStepTypeError
    │   ├── InvalidStepReferenceError
    │   └── FileNotRegisteredError
    └── StepExecutionError
        ├── ColumnNotFoundError         (with fuzzy match suggestion)
        ├── InvalidOperatorError
        ├── JoinKeyMissingError
        ├── AggregationError
        ├── FileReadError
        ├── UnsupportedFileFormatError
        └── StepTimeoutError
"""

import difflib
from typing import Any, Dict, List, Optional


class PipelineIQError(Exception):
    """Base exception for all PipelineIQ errors.

    All subclasses must store structured context and implement to_dict()
    for consistent API error serialization.
    """

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
        }


class PipelineConfigError(PipelineIQError):
    """Base for all pipeline configuration errors."""

    pass


class InvalidYAMLError(PipelineConfigError):
    """Raised when the YAML string cannot be parsed."""

    def __init__(self, yaml_error: str, line: Optional[int] = None) -> None:
        self.yaml_error = yaml_error
        self.line = line
        location = f" at line {line}" if line else ""
        super().__init__(f"Invalid YAML{location}: {yaml_error}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "yaml_error": self.yaml_error,
            "line": self.line,
        }


class MissingRequiredFieldError(PipelineConfigError):
    """Raised when a required field is missing from the pipeline config."""

    def __init__(
        self,
        field: str,
        context: str,
        step_name: Optional[str] = None,
    ) -> None:
        self.field = field
        self.context = context
        self.step_name = step_name
        location = f" in step '{step_name}'" if step_name else ""
        super().__init__(
            f"Missing required field '{field}'{location}: {context}"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "field": self.field,
            "context": self.context,
            "step_name": self.step_name,
        }


class DuplicateStepNameError(PipelineConfigError):
    """Raised when two or more steps share the same name."""

    def __init__(self, step_name: str, occurrences: int) -> None:
        self.step_name = step_name
        self.occurrences = occurrences
        super().__init__(
            f"Duplicate step name '{step_name}' found {occurrences} times. "
            f"Each step must have a unique name."
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "step_name": self.step_name,
            "occurrences": self.occurrences,
        }


class InvalidStepTypeError(PipelineConfigError):
    """Raised when a step specifies an unrecognized type."""

    def __init__(
        self,
        step_name: str,
        invalid_type: str,
        valid_types: List[str],
    ) -> None:
        self.step_name = step_name
        self.invalid_type = invalid_type
        self.valid_types = valid_types
        self.suggestion = _find_closest_match(invalid_type, valid_types)
        hint = f" Did you mean '{self.suggestion}'?" if self.suggestion else ""
        super().__init__(
            f"Step '{step_name}': Invalid step type '{invalid_type}'. "
            f"Valid types: {valid_types}.{hint}"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "step_name": self.step_name,
            "invalid_type": self.invalid_type,
            "valid_types": self.valid_types,
            "suggestion": self.suggestion,
        }


class InvalidStepReferenceError(PipelineConfigError):
    """Raised when a step references a non-existent or later step."""

    def __init__(
        self,
        step_name: str,
        reference_field: str,
        referenced_step: str,
        available_steps: List[str],
    ) -> None:
        self.step_name = step_name
        self.reference_field = reference_field
        self.referenced_step = referenced_step
        self.available_steps = available_steps
        self.suggestion = _find_closest_match(referenced_step, available_steps)
        hint = f" Did you mean '{self.suggestion}'?" if self.suggestion else ""
        super().__init__(
            f"Step '{step_name}': Field '{reference_field}' references "
            f"'{referenced_step}' which does not exist or comes after this step. "
            f"Available prior steps: {available_steps}.{hint}"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "step_name": self.step_name,
            "reference_field": self.reference_field,
            "referenced_step": self.referenced_step,
            "available_steps": self.available_steps,
            "suggestion": self.suggestion,
        }


class FileNotRegisteredError(PipelineConfigError):
    """Raised when a load step references a file_id that hasn't been uploaded."""

    def __init__(
        self,
        step_name: str,
        file_id: str,
        registered_file_ids: List[str],
    ) -> None:
        self.step_name = step_name
        self.file_id = file_id
        self.registered_file_ids = registered_file_ids
        super().__init__(
            f"Step '{step_name}': file_id '{file_id}' is not registered. "
            f"Registered file IDs: {registered_file_ids}"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "step_name": self.step_name,
            "file_id": self.file_id,
            "registered_file_ids": self.registered_file_ids,
        }

class StepExecutionError(PipelineIQError):
    """Base for all errors occurring during step execution."""

    def __init__(self, step_name: str, message: str) -> None:
        self.step_name = step_name
        super().__init__(f"Step '{step_name}': {message}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "step_name": self.step_name,
        }


class ColumnNotFoundError(StepExecutionError):
    """Raised when a referenced column does not exist in the DataFrame.

    Uses fuzzy string matching via difflib.get_close_matches to suggest
    the closest column name, helping users quickly identify typos.
    """

    def __init__(
        self,
        step_name: str,
        column: str,
        available_columns: List[str],
    ) -> None:
        self.column = column
        self.available_columns = available_columns
        self.suggestion = _find_closest_match(column, available_columns)
        hint = (
            f" Did you mean '{self.suggestion}'?"
            if self.suggestion
            else ""
        )
        super().__init__(
            step_name,
            f"Column '{column}' not found. "
            f"Available columns: {available_columns}.{hint}",
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "step_name": self.step_name,
            "column": self.column,
            "available_columns": self.available_columns,
            "suggestion": self.suggestion,
        }


class InvalidOperatorError(StepExecutionError):
    """Raised when a filter step specifies an unsupported operator."""

    def __init__(
        self,
        step_name: str,
        operator: str,
        valid_operators: List[str],
    ) -> None:
        self.operator = operator
        self.valid_operators = valid_operators
        super().__init__(
            step_name,
            f"Invalid operator '{operator}'. "
            f"Valid operators: {valid_operators}",
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "step_name": self.step_name,
            "operator": self.operator,
            "valid_operators": self.valid_operators,
        }


class JoinKeyMissingError(StepExecutionError):
    """Raised when a join key column is missing from one of the DataFrames."""

    def __init__(
        self,
        step_name: str,
        join_key: str,
        side: str,
        available_columns: List[str],
    ) -> None:
        self.join_key = join_key
        self.side = side
        self.available_columns = available_columns
        self.suggestion = _find_closest_match(join_key, available_columns)
        hint = (
            f" Did you mean '{self.suggestion}'?"
            if self.suggestion
            else ""
        )
        super().__init__(
            step_name,
            f"Join key '{join_key}' not found in {side} DataFrame. "
            f"Available columns: {available_columns}.{hint}",
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "step_name": self.step_name,
            "join_key": self.join_key,
            "side": self.side,
            "available_columns": self.available_columns,
            "suggestion": self.suggestion,
        }


class AggregationError(StepExecutionError):
    """Raised when an aggregation operation fails."""

    def __init__(
        self,
        step_name: str,
        column: str,
        function: str,
        reason: str,
    ) -> None:
        self.column = column
        self.function = function
        self.reason = reason
        super().__init__(
            step_name,
            f"Aggregation '{function}' on column '{column}' failed: {reason}",
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "step_name": self.step_name,
            "column": self.column,
            "function": self.function,
            "reason": self.reason,
        }


class FileReadError(StepExecutionError):
    """Raised when a data file cannot be read or parsed."""

    def __init__(
        self,
        step_name: str,
        file_path: str,
        reason: str,
    ) -> None:
        self.file_path = file_path
        self.reason = reason
        super().__init__(
            step_name,
            f"Failed to read file '{file_path}': {reason}",
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "step_name": self.step_name,
            "file_path": self.file_path,
            "reason": self.reason,
        }


class UnsupportedFileFormatError(StepExecutionError):
    """Raised when a file has an unsupported extension."""

    def __init__(
        self,
        step_name: str,
        file_path: str,
        extension: str,
        supported_extensions: List[str],
    ) -> None:
        self.file_path = file_path
        self.extension = extension
        self.supported_extensions = supported_extensions
        super().__init__(
            step_name,
            f"Unsupported file format '{extension}' for '{file_path}'. "
            f"Supported formats: {supported_extensions}",
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "step_name": self.step_name,
            "file_path": self.file_path,
            "extension": self.extension,
            "supported_extensions": self.supported_extensions,
        }


class StepTimeoutError(StepExecutionError):
    """Raised when a step exceeds its allowed execution time."""

    def __init__(
        self,
        step_name: str,
        timeout_seconds: int,
        elapsed_seconds: float,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.elapsed_seconds = elapsed_seconds
        super().__init__(
            step_name,
            f"Step timed out after {elapsed_seconds:.1f}s "
            f"(limit: {timeout_seconds}s)",
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "step_name": self.step_name,
            "timeout_seconds": self.timeout_seconds,
            "elapsed_seconds": self.elapsed_seconds,
        }


def _find_closest_match(
    target: str, candidates: List[str], cutoff: float = 0.6
) -> Optional[str]:
    """Find the closest string match using difflib sequence matching."""
    matches = difflib.get_close_matches(target, candidates, n=1, cutoff=cutoff)
    return matches[0] if matches else None
