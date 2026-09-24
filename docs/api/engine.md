# Engine

::: refresh_engine.RefreshEngine
    options:
      members:
        - refresh
        - submit
        - close

`refresh()` is the convenience entry point. `submit()` accepts a complete
`RefreshRequest`. A `RefreshEngine` owns its coordinator and must be closed.
