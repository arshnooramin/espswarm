"""Base class for hardware exposed to the protocol as named operations."""

from .validation import CommandError, require_keys


class Peripheral:
    capabilities = ()
    # Operation name -> (method name, required arguments, optional arguments).
    operations = {}

    def execute(self, operation, args):
        specification = self.operations.get(operation)
        if specification is None:
            raise CommandError("unsupported_operation", "Operation is not supported")
        method, required, optional = specification
        require_keys(args, required, optional)
        return getattr(self, method)(**args)
