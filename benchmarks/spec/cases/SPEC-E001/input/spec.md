# Purchase Service - Event Handling Specification

## Scope

The Purchase Service records customer purchases and publishes the resulting
events. The following events exist: PurchaseCreated, PurchaseConfirmed,
PurchaseShipped and PurchaseCancelled.

## Event handling

When a PurchaseConfirmed event is published, the fulfilment service updates
the delivery schedule. When a PurchaseCancelled event is published, the
reservation service releases the held stock.

Every consumer subscribes to the event stream and reacts independently. The
specification does not state how two consumers that react to the same event
are coordinated.

## Storage

A purchase is written to the `purchases` table. The write is stamped with the
current time so that the record shows when the purchase happened.

The service also accepts cancellation requests concurrently with the
fulfilment service confirming a purchase. Both paths update the same row.

## Scope limits

Rate limits, performance targets and multi-region replication are not part of
this specification.
