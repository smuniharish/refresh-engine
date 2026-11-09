# Retry policy

The complete user-supplied tenacity policy is configured through
`RetryConfig(retrying=AsyncRetrying(...))`:

```python
from tenacity import AsyncRetrying

from refresh_engine import RetryConfig

retry = RetryConfig(retrying=AsyncRetrying(...))
```

Supply the `AsyncRetrying` configuration that fits the application's policy.
Do not treat individual retry fields as a replacement for the complete
tenacity policy.
