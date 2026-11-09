# Events

`EventHandler` is an abstract base class for asynchronous handlers. An asynchronous
function is also accepted directly; `EventHandlerCallable` and `EventHandlerLike`
describe the accepted handler types. `AsyncEventPublisher` is the concrete
in-process publisher. Its subscription and publication methods are synchronous;
`drain()` is asynchronous.

::: refresh_engine.EventKind

::: refresh_engine.RefreshEvent

::: refresh_engine.EventHandler

::: refresh_engine.EventHandlerCallable

::: refresh_engine.EventHandlerLike

::: refresh_engine.EventPublisher

::: refresh_engine.AsyncEventPublisher
