# Async submission

In async application code, submit directly and await the engine:

```python
result = await engine.submit(request)
```

The public direction is direct async `engine.submit()`; do not add a thread
scheduler bridge to call it. Use the installed package's exact request type and
signature. This reference does not prescribe a scheduler lifecycle or
submission result semantics beyond awaiting the call.
