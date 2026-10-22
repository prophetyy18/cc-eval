# Notification Service - Event Handling Specification

## Scope

The Notification Service sends a single notification per purchase event. The
events it consumes are PurchaseConfirmed and PurchaseCancelled.

## Event handling

The ordering of the events is preserved: the service processes them in the
sequence they were published. A consumer that needs a different ordering must
subscribe to a dedicated topic.

## Failure behaviour

If the push provider rejects a message, the error handling path retries with
an exponential back-off and, after three attempts, moves the message to the
dead-letter topic.

## Concurrency

Two clients may submit concurrently. The service serialises the writes with a
row lock before updating the notification preferences.

## Performance

The service guarantees a latency budget of 200 ms for the 99th percentile of
notifications, measured by the delivery pipeline dashboard.
