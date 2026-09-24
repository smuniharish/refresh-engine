# API reference

The supported imports are listed by `refresh_engine.__all__`. Protocols, models,
strategies, selectors, stores, events, observability helpers, and schedulers are
documented here. User code can import all documented symbols directly from
`refresh_engine`.

- [Engine](engine.md)
- [Extension contracts](contracts.md)
- [Models and configuration](models.md)
- [Fingerprints and selectors](strategies.md)
- [Events, stores, and scheduling](services.md)

Public protocols use structural typing: implementations need the documented
methods, not inheritance. The compatibility guarantee covers the symbols documented
here and exported from `refresh_engine`.
