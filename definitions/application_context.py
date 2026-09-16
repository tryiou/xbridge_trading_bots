class ApplicationContext:
    """Lightweight dependency container for managing application dependencies.

    Provides a simple interface for storing and retrieving dependencies by name,
    enabling injection and decoupling across the codebase.
    """

    def __init__(self):
        self._dependencies: dict[str, object] = {}

    def get(self, name: str, default: object = None) -> object:
        """Retrieve a dependency by name.

        Args:
            name: The name of the dependency.
            default: Value to return if dependency is not found.

        Returns:
            The dependency object, or default if not found.
        """
        return self._dependencies.get(name, default)

    def set(self, name: str, value: object) -> None:
        """Store a dependency by name.

        Args:
            name: The name to store the dependency under.
            value: The dependency object.
        """
        self._dependencies[name] = value

    def has(self, name: str) -> bool:
        """Check if a dependency exists.

        Args:
            name: The name of the dependency.

        Returns:
            True if the dependency exists.
        """
        return name in self._dependencies
