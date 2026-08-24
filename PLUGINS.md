# Developer Guide: Implementing a Custom Plugin Backend in Cactus Runner

This guide provides developers with the technical specification and architectural recommendations needed to implement a custom backend plugin for **Cactus Runner**.

---

## 1. Overview & Architecture

Cactus Runner decouples test orchestration (test steps, actions, checks, evaluation, and reporting) from the underlying server technology (e.g., Envoy server with direct DB access, mock testbeds, REST/gRPC gateways). 

A backend plugin must provide an implementation of the [RunnerBackend](src/cactus_runner/plugin/backends/common.py) protocol along with an [ExpressionResolver](src/cactus_runner/plugin/backends/resolver.py) for evaluating dynamic named variables.

```
┌────────────────────────────────────────────────────────┐
│                   Cactus Runner App                    │
│   (Actions, Checks, Status, Timeline, Finalization)    │
└──────────────────────────┬─────────────────────────────┘
                           │ Consumes RunnerBackend & DTOs
                           ▼
┌────────────────────────────────────────────────────────┐
│                    BackendProvider                     │
│               (apluggy Plugin Manager)                 │
└──────────────────────────┬─────────────────────────────┘
                           │ Calls @hookimpl
                           ▼
┌────────────────────────────────────────────────────────┐
│                  Custom RunnerBackend                  │
│  ┌───────────────────────┐   ┌──────────────────────┐  │
│  │   ExpressionResolver  │   │   Storage / Client   │  │
│  └───────────────────────┘   └──────────────────────┘  │
│  ┌──────────────────────────────────────────────────┐  │
│  │   Mappers (Native Entities <-> Frozen DTOs)      │  │
│  └──────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────┘
```

---

## 2. Backend Provider & `hookimpl` Lifecycle

Plugin discovery and lifecycle management are governed by [`apluggy`](pyproject.toml). The hook specifications are declared in [hookspec.py](src/cactus_runner/plugin/backends/hookspec.py).

### Hook Specification (`BackendSpec`)

The current "project name" for the hookspec is `cactus_runner.backend` where a corresponding hookimpl could be defined using:

```python
import apluggy

project_name = "cactus_runner.backend"
hookimpl = apluggy.HookimplMarker(project_name)
```

To implement a plugin, define a class with `@hookimpl` methods matching [BackendSpec](src/cactus_runner/plugin/backends/hookspec.py):

```python
from cactus_runner.plugin.backends.models import RunnerBackendTestContext
from cactus_runner.plugin.backends.common import RunnerBackend

class MyCustomPlugin:
    @hookimpl
    async def create_backend(self, context: RunnerBackendTestContext | None) -> RunnerBackend:
        """Instantiates and returns the custom RunnerBackend for a test run."""
        return MyCustomBackend(test_context=context)

    @hookimpl
    async def startup(self) -> None:
        """Performs initial startup health checks or connection initialisations before the app starts.
        Raise an exception to halt startup if dependencies are unavailable.
        """
        await verify_external_service_connection()

    @hookimpl
    async def shutdown(self) -> None:
        """Executes cleanup logic during runner shutdown (e.g. closing client sessions, connection pools)."""
        await cleanup_resources()
```

### Context Information ([RunnerBackendTestContext](src/cactus_runner/plugin/backends/models.py))

When `create_backend(context)` is called, the [RunnerBackendTestContext](src/cactus_runner/plugin/backends/models.py) contains immutable metadata about the running test procedure:
* `name`: Name of the test procedure.
* `definition`: The `TestProcedure` object.
* `csip_aus_version`: CSIP-AUS protocol version being tested.
* `client_aggregator_id`, `client_lfdi`, `client_sfdi`: Aggregator / Client Certificate identity.
* `subscription_domain`: Domain configured for subscription ownership verification.
* `initialised_at`, `started_at`: Test lifecycle timestamps.

---

## 3. Data Transfer Objects (DTOs) & Mapping Strategy

The plugin layer uses immutable Data Transfer Objects located in [src/cactus_runner/plugin/dtos/](src/cactus_runner/plugin/dtos/) as the boundary contract. Backends translate internal database records, network schemas, or mock models into these DTOs.

### 3.1. DTO Design Principles
1. **Immutability (`frozen=True`)**: All DTOs are defined with `model_config = ConfigDict(frozen=True)` to prevent unintended mutations during check evaluations and test runs.
2. **String-Encoded Identifiers**: All foreign keys and entity IDs (`site_id`, `site_control_group_id`, `site_reading_type_id`, `subscription_id`, etc.) are string-encoded to remain backend-agnostic (e.g., accommodating UUIDs, integer IDs, or composite keys).
3. **Read vs. Write Separation**:
   * **Read DTOs** (e.g., [Site](src/cactus_runner/plugin/dtos/sites.py), [SiteControl](src/cactus_runner/plugin/dtos/controls.py), [SiteControlGroup](src/cactus_runner/plugin/dtos/controls.py)): Contain full state, backend-assigned IDs, timestamps, and archive/delete markers.
   * **Write DTOs** (e.g., [SiteWrite](src/cactus_runner/plugin/dtos/sites.py), [SiteControlWrite](src/cactus_runner/plugin/dtos/controls.py), [SiteControlGroupWrite](src/cactus_runner/plugin/dtos/controls.py)): Contain only the parameters necessary to create or update resources.

### 3.2. DTO Category Overview

| DTO Module | Key Models | Purpose |
| :--- | :--- | :--- |
| [sites.py](src/cactus_runner/plugin/dtos/sites.py) | `Site`, `SiteWrite`, `SiteFinalReport` | EndDevice / Site registration, LFDI, SFDI, and device categories. |
| [der.py](src/cactus_runner/plugin/dtos/der.py) | `SiteDERSetting`, `SiteDERRating`, `SiteDERStatus`, `SiteDERAvailability` | Inverter ratings, DER settings (limits, ramp rates, modes), and status values. |
| [controls.py](src/cactus_runner/plugin/dtos/controls.py) | `SiteControlGroup`, `SiteControlGroupWrite`, `SiteControl`, `SiteControlWrite`, `SiteControlGroupDefault` | DERProgram / FSA groups, scheduled Dynamic Operating Envelopes (DOEs), and Default Controls. |
| [readings.py](src/cactus_runner/plugin/dtos/readings.py) | `SiteReadingType`, `SiteReading`, `SiteReadingTypeFinalReport` | Mirror meter reading types (UOM, roll/phase flags, multipliers) and interval sample readings. |
| [responses.py](src/cactus_runner/plugin/dtos/responses.py) | `SiteControlResponse` | Client DERControl responses and acknowledgment statuses. |
| [subscriptions.py](src/cactus_runner/plugin/dtos/subscriptions.py) | `Subscription`, `SubscriptionHref`, `TransmitNotificationLog` | Pub/Sub subscription definitions, parsed resource links, and outbound push notification logs. |
| [configs.py](src/cactus_runner/plugin/dtos/configs.py) | `RuntimeConfig`, `RuntimeConfigWrite` | Server runtime parameters and feature flags. |

### 3.3. Implementing Mappers ([mappers.py](src/cactus_runner/plugin/backends/envoy/mappers.py))

Keep all conversion logic isolated in a standalone `mappers.py` file.

#### Inbound Mapping Example (Native Model ➔ DTO):
```python
from cactus_runner.plugin import dtos

def map_native_site_to_dto(native_site: Any) -> dtos.Site:
    return dtos.Site(
        site_id=str(native_site.id),
        nmi=native_site.nmi,
        lfdi=native_site.lfdi,
        sfdi=int(native_site.sfdi),
        device_category=native_site.device_category,
    )
```

#### Outbound Mapping Example (Write DTO ➔ Native Request/Payload):
```python
from cactus_runner.plugin import dtos

def map_site_control_write_to_request(control: dtos.SiteControlWrite) -> dict[str, Any]:
    return {
        "start": int(control.start_time.timestamp()),
        "duration": control.duration_seconds,
        "import_limit_active_watts": control.import_limit_active_watts,
        "export_limit_active_watts": control.export_limit_active_watts,
    }
```

---

## 4. Core Requirements: Action, Check, and Status

To support test execution, your backend implementation must satisfy the requirements of [action.py](src/cactus_runner/app/action.py), [check.py](src/cactus_runner/app/check.py), and [status.py](src/cactus_runner/app/status.py).

### 4.1. Action Core Requirements
Actions represent mutations and configuration changes triggered before or during a test step:

| Method | Description |
| :--- | :--- |
| `reset_state()` | Full wipe and reset of backend state (executed at test initialisation/precondition). |
| `reset_playlist_state()` | Lightweight reset between tests during playlist execution (retaining reusable client entities). |
| `register_aggregator(lfdi, subscription_domain)` | Registers an aggregator client certificate identity and domain; returns aggregator string ID. |
| `register_site(site: SiteWrite)` | Persists an EndDevice / Site (idempotent if LFDI exists). |
| `update_site_post_rate(site_id, post_rate_seconds)` | Modifies the site's `postRate` interval in seconds. |
| `create_site_control_group(group: SiteControlGroupWrite)` | Creates a new DERProgram / FSA group. Returns group ID. |
| `update_site_control_group(group_id, group)` | Updates an existing DERProgram. |
| `remove_function_set_assignment(fsa_id)` | Detaches an FSA association from all DERPrograms referencing it. |
| `get_exclusive_site_group(site_id)` | Retrieves or creates a site group exclusive to the specified site. |
| `create_site_control(site_control_group_id, control: SiteControlWrite)` | Schedules a DERControl (DOE limits, start/duration). |
| `cancel_active_site_controls()` | Cancels all active and scheduled DERControls across all groups. |
| `delete_site_controls_for_group(site_control_group_id)` | Deletes controls belonging to a specific group. |
| `set_site_control_default(site_control_group_id, default: SiteControlGroupDefaultWrite)` | Applies or clears DefaultDERControl limits for a DERProgram. |
| `update_runtime_config(config: RuntimeConfigWrite)` | Applies runtime configuration updates (e.g., server features, timing). |
| `delete_all_site_control_groups()` | Removes all DERPrograms, DERControls, and DefaultDERControls. |
| `delete_site(site_id)` | Deletes a site and exclusive child resources. |

---

### 4.2. Check Core Requirements
Checks inspect backend state and compare it against test definitions:

| Area | Methods | Description |
| :--- | :--- | :--- |
| **Sites** | `get_active_site()`<br>`get_all_sites()` | Returns the most recently modified active site ([Site](src/cactus_runner/plugin/dtos/sites.py)) or all registered sites. |
| **DER** | `get_der_settings(site_id)`<br>`get_der_capability(site_id)`<br>`get_der_status(site_id)` | Fetches DER settings (e.g. `setMaxW`), capability ratings (e.g. `rtgMaxW`), and DER operational status. |
| **Readings** | `get_site_reading_types(site_ids)`<br>`get_site_readings(site_reading_type_ids, start_time, end_time)` | Returns metadata types for meter readings and reading samples within an optional start/end window. |
| **DER Controls** | `get_site_control_groups(fsa_ids)`<br>`get_site_controls()`<br>`get_site_control_group_defaults()`<br>`get_site_control_responses()` | Returns DERProgram groups, historical and active controls, default controls, and client acknowledgment responses ([SiteControlResponse](src/cactus_runner/plugin/dtos/responses.py)). |
| **Subscriptions** | `get_subscription(id)`<br>`get_subscriptions(aggregator_client_id)`<br>`get_notification_logs()` | Fetches registered pub/sub subscriptions and logs of push notifications transmitted to clients. |
| **Configuration** | `get_runtime_config()` | Returns current backend runtime settings. |
| **Parsing** | `parse_subscription_href(href)` | Parses a subscription resource URI into a typed [SubscriptionHref](src/cactus_runner/plugin/dtos/subscriptions.py) (resolving resource type, site ID, resource ID). |

---

### 4.3. Status Core Requirements
Status methods are polled continuously by the UI and test orchestrator:

* `is_healthy() -> bool`: Returns `True` if the backend and its backing services are responsive; `False` otherwise.
* `get_end_device_metadata() -> EndDeviceMetadata | None`: Returns active EndDevice metadata (LFDI, SFDI, device ID, registration pin) used by [status.py](src/cactus_runner/app/status.py) to build `RunnerStatus`.
* `get_expression_resolver() -> ExpressionResolver`: Provides the resolver used for calculating active limit lines (e.g. `upper_max_w`, `lower_max_w`) in real-time UI timeline updates.

---

## 5. Expression Resolvers (`ExpressionResolver` Protocol)

Test definitions frequently use named variable expressions (e.g., `$setMaxW`, `$rtgMaxW`, `$setMaxChargeRateW`). The backend must expose an [ExpressionResolver](src/cactus_runner/plugin/backends/resolver.py) via `backend.get_expression_resolver()`.

### Implementing the Resolver Protocol

All methods return a `float` and must raise `UnresolvableVariableError` if the variable cannot be resolved:

```python
from cactus_runner.app.evaluator import UnresolvableVariableError
from cactus_runner.plugin.backends.resolver import ExpressionResolver

class MyExpressionResolver(ExpressionResolver):
    def __init__(self, server_interface: MyCustomServerInterface) -> None:
        self._admin_session = server_interface

    async def resolve_named_variable_der_setting_max_w(self) -> float:
        site = await self._admin_session.get_active_site()
        if site is None:
            raise UnresolvableVariableError("No active site found")
        der_setting = await self._admin_session.get_der_settings(site.site_id)
        if der_setting is None or der_setting.max_w_value is None:
            raise UnresolvableVariableError("setMaxW is not set")
        return float(der_setting.max_w_value)

    # Implement all other resolve_named_variable_der_setting_* 
    # and resolve_named_variable_der_rating_* methods...
```

---

## 6. Optional Requirements: Final Reporting, Timelines, & Warnings

When a test completes, [finalize.py](src/cactus_runner/app/finalize.py) compiles an artifact bundle containing status summaries, timeline graphs, readings tables, and warning diagnostics.

### 6.1. Serializable Reporting Data
`generate_final_serializable_report_data()` produces serializable DataFrames and schemas:

```python
from cactus_runner.plugin.backends.models import FinalSerializableReportingData

async def generate_final_serializable_report_data(self) -> FinalSerializableReportingData:
    """Returns formatted reporting data including readings as pandas DataFrames."""
    return FinalSerializableReportingData(
        serializable_readings=...,        # dict[SiteReadingTypeFinalReport, pd.DataFrame]
        serializable_reading_counts=..., # dict[SiteReadingTypeFinalReport, int]
        serializable_sites=...,          # list[SiteFinalReport]
    )
```
*If not generating custom reporting tables, return empty collections.*

### 6.2. Post-Test Warnings
`generate_warnings()` performs post-test verification (e.g. checking whether DER settings fluctuated unexpectedly during execution):

```python
from cactus_schema.runner import WarningEntry

async def generate_warnings(self) -> list[WarningEntry]:
    """Inspects historical/archived data to generate post-test warnings."""
    # Return [] if no custom warning analysers are needed
    return []
```

### 6.3. Timeline Generation
The timeline visualizer in [timeline.py](src/cactus_runner/app/timeline.py) is automatically constructed using standard backend methods:
* `get_site_reading_types()` & `get_site_readings()`
* `get_site_controls()`
* `get_site_control_group_defaults()`

---

## 7. Recommended Project Layout & Best Practices

When building a backend plugin package, structure the code similarly to the built-in Envoy backend ([src/cactus_runner/plugin/backends/envoy/](src/cactus_runner/plugin/backends/envoy/backend.py)):

```text
my_backend_plugin/
├── __init__.py          # Plugin registration (@hookimpl) & public exports
├── backend.py           # Core RunnerBackend implementation
├── resolver.py          # ExpressionResolver implementation
├── mappers.py           # Pure mapping functions: Native Models <-> DTOs
├── client.py            # Client / connection pool / session management
└── readings.py          # Readings aggregation & filtering logic
```

### Best Practices

1. **Use Session Factories & Transient Contexts**:
   Avoid holding a single database session open across the entire backend lifetime. Use session factories with `async with session_factory() as session:` to avoid connection leaks and stale queries.

2. **Return Immutable Frozen DTOs**:
   All methods must return the immutable models defined in [src/cactus_runner/plugin/dtos/](src/cactus_runner/plugin/dtos/). Do not leak ORM models or database entities into the app layer.

3. **Query optimization**:
   Inspection of the passed in `RunnerBackendTestContext` should provide significant additional contextual information to be abe to generate targeted queries for backend implementations.
   Filtering is largely baked into the surrounds of a backend call within the core requirements so if there is a doubt in "missing" suitable data via the plugin implementation then opt to return more than is 
   expected to be required.

4. **Isolate Mappers**:
   Keep transformation logic between your native data layer and DTOs; e.g. in a standalone `mappers.py` module.
