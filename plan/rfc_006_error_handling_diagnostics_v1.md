# RFC-006: Error Handling & Diagnostics

**Version:** 1.0
**Status:** Draft
**Author:** JS
**Date:** 2026-02-02

> **Restored 2026-11-21.** The workspace copy of this file had been empty
> since it was committed (`aa3cdac`, 2026-02-03): the original text was
> never actually committed, staged, or stashed, and is unrecoverable from
> git (verified by a full object-database sweep). This restoration comes
> from the operator's copy of the original. Framework references below
> (FastAPI, specific module paths) are advisory illustrations of the era
> it was written in, not prescriptions. Delta alignment between this spec
> and the implemented exception/error-envelope layer is tracked in the
> 2026-11-21 RFC coherence review.

## Abstract

This RFC specifies the error handling architecture for MemoGarden's Internal Python API, HTTP APIs (REST and Semantic), and future FFI boundary for Rust/FilC rewrite. The design uses a two-layer approach: stable error codes at the FFI boundary and rich exception objects at the Python layer, following SQLite's proven pattern for cross-language error handling.

## Motivation

MemoGarden's error handling must satisfy several constraints:

1. **FFI-friendly:** Must map cleanly to C function returns for future Rust/FilC rewrite
2. **Developer-friendly:** Rich exceptions with structured details for debugging
3. **Cross-language consistent:** Same error semantics in Python, TypeScript, Dart
4. **HTTP-compatible:** Clear mapping to HTTP status codes
5. **Stable:** Error codes never change meaning across versions
6. **Diagnostic-rich:** Enough context to debug issues without access to logs

SQLite demonstrates how to achieve all of these: stable integer error codes at the C boundary, with language-specific rich exceptions wrapping them.

## Design Constraints

### FFI Boundary Requirement

The Internal API must eventually become:

```c
// Future C API
typedef struct mg_handle mg_handle;

int mg_add_item(mg_handle *mg, const char *type,
                const char *content_json, const char *metadata_json,
                char **result_json);
// Returns: MG_OK, MG_VALIDATION_FAILED, MG_STORAGE_ERROR, etc.

const char* mg_errmsg(mg_handle *mg);
// Returns: Human-readable error message
```

Python wrapper translates to exceptions:

```python
def add_item(self, type: str, content: dict, /, *, metadata: dict = None) -> Item:
    result_ptr = ctypes.c_char_p()
    code = lib.mg_add_item(self._handle, type.encode(),
                           json.dumps(content).encode(),
                           json.dumps(metadata).encode() if metadata else None,
                           ctypes.byref(result_ptr))

    if code == MG_OK:
        return Item(**json.loads(result_ptr.value.decode()))
    else:
        errmsg = lib.mg_errmsg(self._handle).decode()
        raise _exception_from_code(code, errmsg)
```

This constraint drives our architecture: error codes are primary, exceptions are wrappers.

---

## Layer 1: Error Codes

### Stable Integer Codes

Error codes are **permanently stable** - once assigned, they never change meaning:

```python
# memogarden/error_codes.py

# Success
MG_OK = 0

# General errors (1-99)
MG_ERROR = 1              # Generic error (use specific codes when possible)
MG_INVALID_PARAM = 2      # Invalid parameter value
MG_NOT_FOUND = 3          # Resource not found
MG_ALREADY_EXISTS = 4     # Resource already exists
MG_PERMISSION_DENIED = 5  # Operation not permitted

# Transaction errors (100-199)
MG_TX_ACTIVE = 100        # Already in transaction
MG_TX_NOT_ACTIVE = 101    # Not in transaction
MG_TX_COMMIT_FAILED = 102 # Transaction commit failed
MG_TX_ROLLBACK_FAILED = 103  # Transaction rollback failed

# Schema errors (200-299)
MG_SCHEMA_NOT_FOUND = 200     # Schema not registered
MG_SCHEMA_INVALID = 201       # Schema definition invalid
MG_VALIDATION_FAILED = 202    # Content doesn't match schema
MG_SCHEMA_CONFLICT = 203      # Schema already registered with different definition
MG_SCHEMA_INHERITANCE = 204   # Invalid schema inheritance

# Storage errors (300-399)
MG_STORAGE_ERROR = 300        # Generic database operation failed
MG_STORAGE_CORRUPT = 301      # Database corruption detected
MG_STORAGE_FULL = 302         # Disk full or quota exceeded
MG_STORAGE_LOCKED = 303       # Database locked by another process
MG_STORAGE_READONLY = 304     # Database is read-only

# Relation errors (400-499)
MG_RELATION_NOT_FOUND = 400   # Relation doesn't exist
MG_RELATION_INVALID = 401     # Invalid relation (e.g., self-referencing)
MG_HORIZON_INVALID = 402      # Invalid time horizon format
MG_RELATION_CYCLE = 403       # Circular relation detected (if disallowed)

# Search errors (500-599)
MG_SEARCH_INVALID = 500       # Invalid search parameters
MG_TOKEN_EXPIRED = 501        # Continuation token expired
MG_TOKEN_INVALID = 502        # Invalid continuation token format
MG_SEARCH_TIMEOUT = 503       # Search exceeded time limit
MG_SEARCH_TOO_BROAD = 504     # Search scope too large

# System errors (900-999)
MG_INTERNAL_ERROR = 900       # Internal system error (likely a bug)
MG_NOT_IMPLEMENTED = 901      # Feature not yet implemented
MG_DEPRECATED = 902           # Using deprecated feature
MG_VERSION_MISMATCH = 903     # Incompatible versions
```

### Code Ranges

Error codes are organized in ranges for clarity:

| Range | Category | Purpose |
|-------|----------|---------|
| 0 | Success | MG_OK only |
| 1-99 | General | Common errors (not found, invalid param) |
| 100-199 | Transaction | Transaction state errors |
| 200-299 | Schema | Schema and validation errors |
| 300-399 | Storage | Database and I/O errors |
| 400-499 | Relation | Graph operation errors |
| 500-599 | Search | Query and search errors |
| 900-999 | System | Internal and system errors |

**Reserved ranges for future use:** 600-899

### Stability Guarantees

1. **Error codes never change meaning** - MG_NOT_FOUND will always mean "not found"
2. **Codes can be deprecated but not removed** - Old codes remain for backward compatibility
3. **New codes can be added** - Within reserved ranges or new ranges
4. **Ranges are organized** - Related errors grouped together

---

## Layer 2: Exception Classes

### Base Exception

All MemoGarden exceptions inherit from a common base:

```python
# memogarden/exceptions.py

class MemoGardenError(Exception):
    """Base exception for all MemoGarden errors."""

    def __init__(self, message: str, code: int, details: Optional[dict] = None):
        """
        Initialize MemoGarden error.

        Args:
            message: Human-readable error message
            code: Error code from error_codes.py
            details: Structured error details (machine-readable)
        """
        self.message = message
        self.code = code
        self.details = details or {}
        super().__init__(message)

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(code={self.code}, message={self.message!r})"

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            'type': self.__class__.__name__,
            'code': self.code,
            'message': self.message,
            'details': self.details
        }
```

### Exception Hierarchy

```python
# General errors
class InvalidParameterError(MemoGardenError):
    """Invalid parameter value provided."""
    def __init__(self, param_name: str, value: Any, reason: str):
        super().__init__(
            f"Invalid parameter '{param_name}': {reason}",
            code=MG_INVALID_PARAM,
            details={'param': param_name, 'value': str(value), 'reason': reason}
        )

class NotFoundError(MemoGardenError):
    """Requested resource not found."""
    def __init__(self, resource_type: str, identifier: str):
        super().__init__(
            f"{resource_type} not found: {identifier}",
            code=MG_NOT_FOUND,
            details={'resource_type': resource_type, 'identifier': identifier}
        )

class AlreadyExistsError(MemoGardenError):
    """Resource already exists."""
    def __init__(self, resource_type: str, identifier: str):
        super().__init__(
            f"{resource_type} already exists: {identifier}",
            code=MG_ALREADY_EXISTS,
            details={'resource_type': resource_type, 'identifier': identifier}
        )


# Transaction errors
class TransactionError(MemoGardenError):
    """Base class for transaction-related errors."""
    pass

class AlreadyInTransactionError(TransactionError):
    """Cannot begin transaction - already in one."""
    def __init__(self):
        super().__init__(
            "Already in transaction. Commit or rollback current transaction first.",
            code=MG_TX_ACTIVE
        )

class NotInTransactionError(TransactionError):
    """Cannot commit/rollback - not in transaction."""
    def __init__(self, operation: str):
        super().__init__(
            f"Not in transaction. Cannot {operation}.",
            code=MG_TX_NOT_ACTIVE,
            details={'operation': operation}
        )

class TransactionCommitError(TransactionError):
    """Transaction commit failed."""
    def __init__(self, reason: str):
        super().__init__(
            f"Transaction commit failed: {reason}",
            code=MG_TX_COMMIT_FAILED,
            details={'reason': reason}
        )


# Schema errors
class SchemaError(MemoGardenError):
    """Base class for schema-related errors."""
    pass

class SchemaNotFoundError(SchemaError):
    """Schema not registered for type."""
    def __init__(self, type_name: str):
        super().__init__(
            f"Schema not found for type: {type_name}",
            code=MG_SCHEMA_NOT_FOUND,
            details={'type_name': type_name}
        )

class ValidationError(SchemaError):
    """Content validation against schema failed."""
    def __init__(self, type_name: str, errors: List[str]):
        error_list = '\n  - '.join(errors)
        super().__init__(
            f"Validation failed for {type_name}:\n  - {error_list}",
            code=MG_VALIDATION_FAILED,
            details={'type_name': type_name, 'errors': errors}
        )

class SchemaConflictError(SchemaError):
    """Schema already registered with different definition."""
    def __init__(self, type_name: str, reason: str):
        super().__init__(
            f"Schema conflict for {type_name}: {reason}",
            code=MG_SCHEMA_CONFLICT,
            details={'type_name': type_name, 'reason': reason}
        )


# Storage errors
class StorageError(MemoGardenError):
    """Base class for storage-related errors."""
    pass

class DatabaseLockError(StorageError):
    """Database locked by another process."""
    def __init__(self, db_path: str, timeout_seconds: Optional[int] = None):
        msg = f"Database locked: {db_path}"
        if timeout_seconds:
            msg += f" (waited {timeout_seconds}s)"
        super().__init__(
            msg,
            code=MG_STORAGE_LOCKED,
            details={'db_path': db_path, 'timeout_seconds': timeout_seconds}
        )

class StorageCorruptError(StorageError):
    """Database corruption detected."""
    def __init__(self, db_path: str, diagnostic: str):
        super().__init__(
            f"Database corruption detected in {db_path}: {diagnostic}",
            code=MG_STORAGE_CORRUPT,
            details={'db_path': db_path, 'diagnostic': diagnostic}
        )


# Relation errors
class RelationError(MemoGardenError):
    """Base class for relation-related errors."""
    pass

class InvalidRelationError(RelationError):
    """Relation is semantically invalid."""
    def __init__(self, reason: str, **details):
        super().__init__(
            f"Invalid relation: {reason}",
            code=MG_RELATION_INVALID,
            details={'reason': reason, **details}
        )

class InvalidHorizonError(RelationError):
    """Time horizon format is invalid."""
    def __init__(self, horizon: str, expected_format: str = "e.g., '6M', '1Y', '2W'"):
        super().__init__(
            f"Invalid time horizon '{horizon}'. Expected format: {expected_format}",
            code=MG_HORIZON_INVALID,
            details={'horizon': horizon, 'expected_format': expected_format}
        )


# Search errors
class SearchError(MemoGardenError):
    """Base class for search-related errors."""
    pass

class InvalidSearchParametersError(SearchError):
    """Search parameters are invalid."""
    def __init__(self, reason: str, **details):
        super().__init__(
            f"Invalid search parameters: {reason}",
            code=MG_SEARCH_INVALID,
            details={'reason': reason, **details}
        )

class TokenExpiredError(SearchError):
    """Continuation token has expired."""
    def __init__(self, token: str, expiry: Optional[datetime] = None):
        super().__init__(
            "Continuation token has expired",
            code=MG_TOKEN_EXPIRED,
            details={'token': token, 'expiry': expiry.isoformat() if expiry else None}
        )

class TokenInvalidError(SearchError):
    """Continuation token format is invalid."""
    def __init__(self, token: str, reason: str):
        super().__init__(
            f"Invalid continuation token: {reason}",
            code=MG_TOKEN_INVALID,
            details={'token': token, 'reason': reason}
        )


# System errors
class InternalError(MemoGardenError):
    """Internal system error (likely a bug)."""
    def __init__(self, message: str, context: Optional[dict] = None):
        super().__init__(
            f"Internal error: {message}",
            code=MG_INTERNAL_ERROR,
            details=context or {}
        )

class NotImplementedError(MemoGardenError):
    """Feature not yet implemented."""
    def __init__(self, feature: str):
        super().__init__(
            f"Not implemented: {feature}",
            code=MG_NOT_IMPLEMENTED,
            details={'feature': feature}
        )
```

---

## Error Handling Patterns

### Pattern 1: Query Methods Return None

For query operations, "not found" is a **normal outcome**, not an error:

```python
# ✓ Good: Returns None for not found
def get_item(self, uuid: str, /) -> Optional[Item]:
    """Retrieve item by UUID. Returns None if not found."""
    result = self._soil.query_item(uuid)
    if result is None:
        return None
    return Item(**result)

# Usage
item = mg.get_item(uuid)
if item is None:
    print("Item not found")
else:
    print(f"Found: {item.type}")
```

**Applies to:**
- `get_item()` → None if not found
- `get_entity()` → None if not found
- `get_relation()` → None if not found
- `get_schema()` → None if not registered

**Rationale:** Query methods are exploratory. "Not found" is expected behavior, not exceptional.

### Pattern 2: Invalid Input Raises Immediately

Programmer errors should **fail fast**:

```python
# ✓ Good: Raises for invalid parameters
def relate(self, from_uuid: str, to_uuid: str, rel_type: str, /, *,
           horizon: Optional[str] = None, metadata: Optional[dict] = None) -> Relation:
    # Validate self-referencing
    if from_uuid == to_uuid:
        raise InvalidRelationError(
            "Cannot create self-referencing relation",
            from_uuid=from_uuid,
            to_uuid=to_uuid
        )

    # Validate horizon format
    if horizon and not is_valid_horizon_format(horizon):
        raise InvalidHorizonError(horizon)

    # Validate UUIDs exist
    if not self._exists(from_uuid):
        raise NotFoundError("Item or Entity", from_uuid)
    if not self._exists(to_uuid):
        raise NotFoundError("Item or Entity", to_uuid)

    # Now safe to create relation
    return self._create_relation(from_uuid, to_uuid, rel_type, horizon, metadata)
```

**Applies to:**
- Invalid parameter types/values
- Constraint violations (self-referencing relations)
- State violations (transaction already active)
- Schema mismatches

**Rationale:** Invalid input indicates bugs in calling code. Fail fast to catch issues early.

### Pattern 3: System Errors Always Raise

System-level failures are **unexpected**:

```python
# ✓ Good: Wraps and raises storage errors
def _execute_sql(self, sql: str, params: tuple = ()):
    try:
        return self._conn.execute(sql, params)
    except sqlite3.OperationalError as e:
        error_msg = str(e).lower()

        if "database is locked" in error_msg:
            raise DatabaseLockError(self._db_path, timeout_seconds=5)

        if "disk i/o error" in error_msg or "database disk image is malformed" in error_msg:
            raise StorageCorruptError(self._db_path, str(e))

        # Generic storage error
        raise StorageError(str(e), code=MG_STORAGE_ERROR)

    except Exception as e:
        # Unexpected error - likely a bug
        raise InternalError(
            f"Unexpected error during SQL execution: {e}",
            context={'sql': sql[:100], 'error_type': type(e).__name__}
        )
```

**Applies to:**
- Database failures
- I/O errors
- Corruption detected
- Out of memory
- Internal assertion failures

**Rationale:** System failures require intervention. User can't fix them programmatically.

### Pattern 4: Validate Early

Validation happens **before mutation**:

```python
def add_item(self, type: str, content: dict, /, *,
             metadata: Optional[dict] = None) -> Item:
    # Validate schema if registered
    if self.has_schema(type):
        validation = self.validate_content(type, content)
        if not validation.valid:
            raise ValidationError(type, validation.errors)

    # Validate metadata format if present
    if metadata and not isinstance(metadata, dict):
        raise InvalidParameterError('metadata', metadata, 'Must be a dictionary')

    # All validation passed - safe to mutate
    item_data = self._soil.add_item(type, content, metadata or {})
    return Item(**item_data)
```

**Rationale:** Catch problems before database modifications. Easier to recover, cleaner error messages.

### Pattern 5: Context Manager Cleanup

Ensure cleanup **even on errors**:

```python
class MemoGarden:
    def __enter__(self) -> 'MemoGarden':
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        try:
            # Rollback transaction if active and exception occurred
            if self.in_transaction() and exc_type is not None:
                try:
                    self.rollback_transaction()
                except Exception as e:
                    # Log rollback failure but don't suppress original exception
                    logger.error(f"Rollback failed during cleanup: {e}")
        finally:
            # Always close, even if rollback failed
            try:
                self.close()
            except Exception as e:
                logger.error(f"Close failed during cleanup: {e}")

        # Don't suppress the original exception
        return False

# Usage
with MemoGarden() as mg:
    mg.begin_transaction()
    mg.add_item('Note', {'text': '...'})
    raise ValueError("Something went wrong")
    # Transaction auto-rolled-back, handle auto-closed
```

**Rationale:** Resource leaks are bugs. Always cleanup, even during error conditions.

---

## HTTP API Error Mapping

### REST API Status Codes

Standard HTTP status codes for REST endpoints:

```python
# memogarden-api/api/error_handlers.py

from fastapi import Request, status
from fastapi.responses import JSONResponse
from memogarden.exceptions import *

@app.exception_handler(MemoGardenError)
async def memogarden_error_handler(request: Request, exc: MemoGardenError):
    """Map MemoGarden exceptions to HTTP responses."""

    # Exception type → HTTP status code mapping
    status_map = {
        # 4xx Client Errors
        NotFoundError: status.HTTP_404_NOT_FOUND,
        ValidationError: status.HTTP_400_BAD_REQUEST,
        InvalidParameterError: status.HTTP_400_BAD_REQUEST,
        InvalidRelationError: status.HTTP_400_BAD_REQUEST,
        InvalidHorizonError: status.HTTP_400_BAD_REQUEST,
        InvalidSearchParametersError: status.HTTP_400_BAD_REQUEST,
        TokenInvalidError: status.HTTP_400_BAD_REQUEST,
        AlreadyExistsError: status.HTTP_409_CONFLICT,
        AlreadyInTransactionError: status.HTTP_409_CONFLICT,
        SchemaConflictError: status.HTTP_409_CONFLICT,
        TokenExpiredError: status.HTTP_410_GONE,
        NotImplementedError: status.HTTP_501_NOT_IMPLEMENTED,

        # 5xx Server Errors
        StorageError: status.HTTP_500_INTERNAL_SERVER_ERROR,
        DatabaseLockError: status.HTTP_503_SERVICE_UNAVAILABLE,
        StorageCorruptError: status.HTTP_500_INTERNAL_SERVER_ERROR,
        InternalError: status.HTTP_500_INTERNAL_SERVER_ERROR,
        TransactionCommitError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    }

    status_code = status_map.get(type(exc), status.HTTP_500_INTERNAL_SERVER_ERROR)

    return JSONResponse(
        status_code=status_code,
        content={
            "error": exc.to_dict()
        }
    )
```

### Error Response Format

All error responses follow consistent JSON structure:

```json
{
  "error": {
    "type": "ValidationError",
    "code": 202,
    "message": "Validation failed for Email:\n  - missing required field 'subject'\n  - 'from' must be valid email",
    "details": {
      "type_name": "Email",
      "errors": [
        "missing required field 'subject'",
        "'from' must be valid email"
      ]
    }
  }
}
```

### Example HTTP Responses

**404 Not Found:**
```json
{
  "error": {
    "type": "NotFoundError",
    "code": 3,
    "message": "Item not found: uuid-12345",
    "details": {
      "resource_type": "Item",
      "identifier": "uuid-12345"
    }
  }
}
```

**400 Bad Request (Invalid Parameter):**
```json
{
  "error": {
    "type": "InvalidRelationError",
    "code": 401,
    "message": "Invalid relation: Cannot create self-referencing relation",
    "details": {
      "reason": "Cannot create self-referencing relation",
      "from_uuid": "uuid-123",
      "to_uuid": "uuid-123"
    }
  }
}
```

**410 Gone (Token Expired):**
```json
{
  "error": {
    "type": "TokenExpiredError",
    "code": 501,
    "message": "Continuation token has expired",
    "details": {
      "token": "search_abc123",
      "expiry": "2026-02-02T15:30:00Z"
    }
  }
}
```

**503 Service Unavailable (Database Locked):**
```json
{
  "error": {
    "type": "DatabaseLockError",
    "code": 303,
    "message": "Database locked: /var/lib/memogarden/soil.db (waited 5s)",
    "details": {
      "db_path": "/var/lib/memogarden/soil.db",
      "timeout_seconds": 5
    }
  }
}
```

---

## Logging Strategy

### Log Levels by Error Severity

```python
import logging
logger = logging.getLogger('memogarden')

def add_item(self, type: str, content: dict, /, *, metadata: dict = None) -> Item:
    try:
        # DEBUG: Trace successful operations
        logger.debug(f"Adding item: type={type}")

        # Validate
        if self.has_schema(type):
            validation = self.validate_content(type, content)
            if not validation.valid:
                # INFO: Expected validation failures
                logger.info(f"Validation failed for {type}: {validation.errors}")
                raise ValidationError(type, validation.errors)

        # Add item
        item = self._soil.add_item(type, content, metadata or {})

        # DEBUG: Successful completion
        logger.debug(f"Item added: {item.uuid}")
        return item

    except ValidationError:
        # Already logged at INFO, just re-raise
        raise

    except StorageError as e:
        # ERROR: System failures
        logger.error(f"Storage error during add_item: {e}", exc_info=True)
        raise

    except Exception as e:
        # CRITICAL: Unexpected errors (bugs)
        logger.critical(f"Unexpected error in add_item: {e}", exc_info=True)
        raise InternalError(str(e), {'operation': 'add_item', 'type': type})
```

**Log level guidelines:**

| Level | Usage | Examples |
|-------|-------|----------|
| DEBUG | Trace execution, successful operations | "Item added", "Query executed" |
| INFO | Expected errors, normal state changes | "Validation failed", "Transaction started" |
| WARNING | Recoverable issues, deprecated features | "Using deprecated API", "Retrying operation" |
| ERROR | System errors, storage failures | "Database locked", "I/O error" |
| CRITICAL | Internal errors, corruption, bugs | "Unexpected exception", "Data corruption" |

### Structured Logging

Use structured fields for machine parsing:

```python
logger.info("item_created", extra={
    'uuid': item.uuid,
    'type': item.type,
    'metadata': item.metadata,
    'duration_ms': duration
})

logger.error("storage_error", extra={
    'error_code': e.code,
    'db_path': self._db_path,
    'operation': 'add_item'
}, exc_info=True)
```

---

## Cross-Language Consistency

### Error Codes Must Be Identical

All language implementations use the same error codes:

**Python:**
```python
MG_NOT_FOUND = 3
raise NotFoundError("Item", uuid)
```

**TypeScript (future):**
```typescript
const MG_NOT_FOUND = 3;
throw new NotFoundError("Item", uuid);
```

**Dart (future):**
```dart
const int MG_NOT_FOUND = 3;
throw NotFoundError("Item", uuid);
```

**C (future FFI):**
```c
#define MG_NOT_FOUND 3
return MG_NOT_FOUND;
```

### Exception Mapping

Each language provides equivalent exceptions:

| Error Code | Python | TypeScript | Dart | C Return |
|------------|--------|------------|------|----------|
| MG_NOT_FOUND | `NotFoundError` | `NotFoundError` | `NotFoundError` | `MG_NOT_FOUND` |
| MG_VALIDATION_FAILED | `ValidationError` | `ValidationError` | `ValidationError` | `MG_VALIDATION_FAILED` |
| MG_TX_ACTIVE | `AlreadyInTransactionError` | `AlreadyInTransactionError` | `AlreadyInTransactionError` | `MG_TX_ACTIVE` |

### Error Details Structure

All languages use consistent detail fields:

```python
# Python
{
    'type_name': 'Email',
    'errors': ['missing required field']
}
```

```typescript
// TypeScript
{
    typeName: 'Email',
    errors: ['missing required field']
}
```

```dart
// Dart
{
    'typeName': 'Email',
    'errors': ['missing required field']
}
```

Field naming follows each language's convention (snake_case Python, camelCase TS/Dart), but fields are semantically equivalent.

---

## Testing Error Handling

### Test Categories

**1. Expected errors (query returns None):**
```python
def test_get_item_not_found():
    mg = MemoGarden(profile='memory')
    item = mg.get_item("nonexistent-uuid")
    assert item is None  # Not an exception
```

**2. Validation errors:**
```python
def test_validation_error_structure():
    mg = MemoGarden(profile='memory')
    mg.register_schema('Email', email_schema)

    with pytest.raises(ValidationError) as exc_info:
        mg.add_item('Email', {'invalid': 'content'})

    err = exc_info.value
    assert err.code == MG_VALIDATION_FAILED
    assert err.details['type_name'] == 'Email'
    assert len(err.details['errors']) > 0
```

**3. State violations:**
```python
def test_transaction_already_active():
    mg = MemoGarden(profile='memory')
    mg.begin_transaction()

    with pytest.raises(AlreadyInTransactionError) as exc_info:
        mg.begin_transaction()

    assert exc_info.value.code == MG_TX_ACTIVE
    assert mg.in_transaction()  # State remains consistent
```

**4. Invalid input:**
```python
def test_self_referencing_relation():
    mg = MemoGarden(profile='memory')
    item = mg.add_item('Note', {'text': 'test'})

    with pytest.raises(InvalidRelationError) as exc_info:
        mg.relate(item.uuid, item.uuid, 'self_link')

    err = exc_info.value
    assert err.code == MG_RELATION_INVALID
    assert 'self-referencing' in err.message.lower()
```

**5. HTTP mapping:**
```python
@pytest.mark.asyncio
async def test_http_error_not_found(client):
    response = await client.get("/apps/test-app/items/nonexistent")

    assert response.status_code == 404
    data = response.json()
    assert data['error']['code'] == MG_NOT_FOUND
    assert data['error']['type'] == 'NotFoundError'
```

### Test Coverage Requirements

All error codes must have:
- Unit test triggering the error
- Verification of error code
- Verification of error details structure
- HTTP mapping test (if applicable)

---

## Implementation Notes

### Error Code Registry

Maintain central registry to prevent code collisions:

```python
# memogarden/error_codes.py

# This file is the authoritative source for error codes.
# DO NOT define error codes elsewhere.
# Before adding a new code, check this file to avoid conflicts.

_ERROR_CODE_REGISTRY = {
    0: ("MG_OK", "Success"),
    1: ("MG_ERROR", "Generic error"),
    2: ("MG_INVALID_PARAM", "Invalid parameter"),
    3: ("MG_NOT_FOUND", "Resource not found"),
    # ... all codes documented
}

def validate_error_code(code: int, name: str) -> None:
    """Validate error code assignment at module load time."""
    if code in _ERROR_CODE_REGISTRY:
        registered_name, _ = _ERROR_CODE_REGISTRY[code]
        if registered_name != name:
            raise ValueError(
                f"Error code collision: {code} already registered as "
                f"{registered_name}, cannot register as {name}"
            )
```

### Exception Factory

For FFI boundary, create exceptions from error codes:

```python
def exception_from_code(code: int, message: str, details: dict = None) -> MemoGardenError:
    """Create appropriate exception from error code."""

    exception_map = {
        MG_NOT_FOUND: lambda: NotFoundError(
            details.get('resource_type', 'Resource'),
            details.get('identifier', 'unknown')
        ),
        MG_VALIDATION_FAILED: lambda: ValidationError(
            details.get('type_name', 'unknown'),
            details.get('errors', [message])
        ),
        # ... all codes mapped
    }

    factory = exception_map.get(code)
    if factory:
        return factory()

    # Fallback: generic error with code
    return MemoGardenError(message, code, details)
```

---

## Future Extensions

### Metrics & Monitoring

Track error rates by code:

```python
metrics.increment(f"error.code.{exc.code}")
metrics.increment(f"error.type.{type(exc).__name__}")
```

### Error Recovery Strategies

Some errors may be recoverable:

```python
@retry(max_attempts=3, retry_on=[DatabaseLockError])
def add_item_with_retry(mg, type, content):
    return mg.add_item(type, content)
```

### Debugging Tools

Error details enable rich debugging:

```python
# CLI tool: memogarden debug-error
$ memogarden debug-error --code 303 --details '{"db_path": "/var/lib/memogarden/soil.db"}'

Error: DatabaseLockError (code 303)
Message: Database locked: /var/lib/memogarden/soil.db
Suggestions:
  - Check for other MemoGarden processes: `ps aux | grep memogarden`
  - Check for stale lock files: `fuser /var/lib/memogarden/soil.db`
  - Increase timeout in configuration: `lock_timeout_seconds = 30`
```

---

## References

- SQLite Error Codes: https://www.sqlite.org/rescode.html
- Python Exception Hierarchy: https://docs.python.org/3/library/exceptions.html
- HTTP Status Codes: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status
- RFC-005 v3: MemoGarden API Design
- RFC-004 v1: Package Structure & Deployment

---

## Revision History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-02-02 | Initial specification |
| 1.0 (restored) | 2026-11-21 | Text restored verbatim from the operator's copy; the committed file had been empty since 2026-02-03 (`aa3cdac`) and the original was unrecoverable from git. Restoration note added; no substantive changes. |

---

**Status:** Draft
**Next Steps:**
1. Review and approve error handling architecture
2. Implement error codes module
3. Implement exception classes
4. Add error handling to Internal API methods
5. Implement HTTP error mapping
6. Write comprehensive error handling tests
7. Document error codes in API reference

---

**END OF RFC**
