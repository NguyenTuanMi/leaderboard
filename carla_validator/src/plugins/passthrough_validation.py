from .validation_base import ValidationBase
from .validation_helper import ValidationContext, ValidationOutput

class PassThroughValidation(ValidationBase):

    def validate(self, context: ValidationContext) -> ValidationOutput:
        return ValidationOutput(
            valid=True,
            errors=[],
            details={"message": "pass-through validation plugin always returns valid"},
        )