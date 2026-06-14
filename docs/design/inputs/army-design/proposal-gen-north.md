# Pigeon Multi-Model "Army" Support Design Proposal

## Overview
This proposal introduces first-class multi-model support for pigeon, enabling coordinated execution across multiple AI model providers with native model selection, pool management, and cross-wave artifact handling.

## (a) Model Field + Placeholder vs. One-Runner-Per-Model

### Current Architecture
- **Runner per task**: Each task specifies `runner: claude`, `runner: agy`, or `runner: opencode`
- **Command templates** in config.yaml: `"claude": ["claude", "-p", "{prompt}"]`
- **Default runner assignment**: Single string assigns to all, list round-robin

### New Model Field
Add `model: <provider/model>` to tasks definition:

```yaml
- id: analyze
  model: opencode/nemotron-3-ultra-free
  doing: analyze data patterns
  needs: [fetch-data]
```

**Model Resolution Strategy**:
1. **Explicit model + explicit runner**: Use runner's model override flag (e.g., opencode -m provider/model)
2. **Explicit model + default runner**: Default runner receives `{model}` placeholder in its template
3. **No model + single-model setup**: Uses default runner template (existing behavior)
4. **No model + multi-model setup**: Fails (explicit model required for armies)

### Model Placeholder in Templates
Update runner templates to support `{model}` placeholder:

```yaml
coordinate.runners:
  claude: ["claude", "-p", "{prompt}", "--model", "{model}"]
  agy: ["agy", "-p", "{prompt}", "--model", "{model}"]
  opencode: ["opencode", "run", "{prompt}", "-m", "{model}"]
```

## (b) Named Model Pools + Round-Robin Across Free Providers

### Model Pool Configuration
Add `model_pools:` to coordinate configuration:

```yaml
coordinate:
  model_pools:
    free_models:
      - opencode/nemotron-3-ultra-free
      - opencode/deepseek-r1-distill-llama-70b
      - opencode/mimo-7b
    premium_models:
      - openai/gpt-4o
      - anthropic/claude-3-5-sonnet
```

### Pool Selection Strategies
1. **Explicit model**: Task's `model:` field selects specific model
2. **Pool-based selection**: `model_pool: free_models` selects from pool
3. **Round-robin**: Load distribution across pool members
4. **Weighted distribution**: Different pools have different weights

### Free Model Handling
- **Zero-cost models**: No USD budget required (`budget.usd: 0.0`)
- **Rate limit tracking**: Per-model token/hour counters
- **Clock-based throttling**: Wall-clock rate limits alongside token limits

## (c) Army -> Gate(Concordance) -> Verdict Topology

### Army Structure
An "army" is a set of tasks sharing:
- Common `model_pool` or explicit models
- Same dependency graph (needs/crew structure)
- Coordinated execution waves

### Army Execution Waves
```
Wave N-1: Army of M models produces M sets of artifacts
Wave N: Gates run independently on each artifact set
Wave N+1: Verdict reconciles all N-1 wave results
```

### Existing Integration Points
Leverage existing pigeon structures:

**Dependency Graph (`needs:`)**
- Army tasks can depend on each other
- Gates receive artifacts from specific army members

**Crew Verdict Gates**
- Subagent verdicts operate at army level
- Concordance gates validate across army members

### Army Configuration Example
```yaml
sid: multi_model-army
session: text-to-sql
species: army
model_pool: free_models
army:
  wave_count: 3
  coordination: concurrent
  artifacts_per_model: 1
  gates:
    - triage
    - concord
    verdict
```

## (d) Cross-Wave POINTERS-NOT-PAYLOADS

### Current Limitation
Today: All handoffs built up-front, before downstream artifacts exist.

### POINTERS-NOT-PAYLOADS Design
**Wave N-1 → Wave N Communication**:
1. **Artifacts as pointers**: Each army member publishes `artifact:` pointer to its work
2. **Lazy resolution**: Wave N retrieves pointers as needed
3. **No duplication**: Full payload transferred once, pointers reference work

### Implementation Pattern
```yaml
# Army task configuration
artifacts:
  - "repo://army-members/gen-nemotron/design.md"
  - "repo://army-members/gen-deepseek/design.md"
  - "repo://army-members/gen-mimo/design.md"

# Gate task receives pointers, resolves as needed
needs:
  - ["gen-nemotron", "gen-deepseek", "gen-mimo"]
```

### Pointer Resolution
```python
def resolve_pointer(pointer: str) -> str:
    if pointer.startswith("repo://army-members/"):
        model = extract_model_from_path(pointer)
        return f"model://{model}/{pointer}"
    return pointer
```

## (e) Telemetry + Rate-Limit Handling for FREE Models

### Budget Model Refactoring
Current: `budget.usd` binds ALL runs (no FREE model support)

New: **Per-model budget boundaries**:
```yaml
coordinate.budget:
  global:
    tokens: 100000
    usd: 0.0              # global ceiling ignored for FREE models
  models:
    "opencode/nemotron-3-ultra-free":
      tokens: null        # no limit
      usd: 0.0
      rate_limit: {tokens_per_hour: 1000}
```

### Rate-Limit Architecture
```python
class ModelRateLimitTracker:
    def __init__(self, model: str, tokens_per_hour: int):
        self.model = model
        self.limit = tokens_per_hour
        self.hourly_tokens = Counter()
        self.clock = Clock()
    
    def can_consume(self, tokens: int) -> bool:
        self.hourly_tokens[self.clock.current_hour] -= self.clock.last_reset
        return self.hourly_tokens[self.clock.current_hour] + tokens <= self.limit
```

### Free Model Enforcement
```python
def enforce_free_model_limits(
    model: str, 
    tokens_consumed: int, 
    config: Config
) -> tuple[bool, str]:
    if not model.startswith("opencode/"):
        return True, ""
    
    tracker = get_rate_limit_tracker(model)
    if not tracker.can_consume(tokens_consumed):
        return False, f"Rate limit exceeded for {model}"
    
    return True, ""
```

### Wall-Clock Rate Limits
```yaml
coordinate:
  rate_limits:
    free_models:
      tokens_per_hour: 1000
      tokens_per_day: 10000
      reset_window: "hourly"
```

## Configuration Schema Changes

### Updated Default Config Structure
```python
def default_config(contract_dir: str = LEGACY_CONTRACT_DIR) -> dict[str, Any]:
    return {
        # ... existing ...
        "coordinate": {
            # ... existing ...
            "model_pools": {
                "free_models": [],
                "premium_models": []
            },
            "rate_limits": {
                "free_models": {
                    "tokens_per_hour": 1000,
                    "tokens_per_day": 10000,
                    "reset_window": "hourly"
                }
            },
            "runners": {
                "claude": ["claude", "-p", "{prompt}", "--model", "{model}"],
                "agy": ["agy", "-p", "{prompt}", "--model", "{model}"],
                "opencode": ["opencode", "run", "{prompt}", "-m", "{model}"]
            }
        }
    }
```

## Migration Path

### Phase 1: Model Field Support (Weeks 1-2)
- Add `model:` field to task definition
- Update runner templates with `{model}` placeholder
- Validate model against provider registry

### Phase 2: Model Pool Integration (Weeks 3-4)
- Add `model_pool:` field to tasks
- Implement pool selection logic
- Add round-robin distribution

### Phase 3: Army Topology (Weeks 5-6)
- Implement army coordination layer
- Add cross-wave pointer handling
- Integrate with existing needs/crew systems

### Phase 4: Free Model Support (Weeks 7-8)
- Add per-model rate limiting
- Implement clock-based throttling
- Update budget tracking for FREE models

## Technical Implementation Notes

### Backward Compatibility
- Existing tasks without `model:` field continue to work
- Default runner behavior unchanged for single-model tasks
- Army mode opt-in only

### Performance Considerations
- Model resolution on each task dispatch (cache results)
- Pointer resolution lazy evaluation
- Rate limit tracking per-model (memory efficient)

### Monitoring & Metrics
- Track model usage across runs
- Monitor rate limit hits vs. hard limits
- Visibility into army coordination efficiency

## Benefits

1. **Native multi-model support**: No workarounds needed
2. **Flexible coordination**: From single-model to army-scale execution
3. **Cost-efficient**: Native support for FREE models
4. **Extensible**: Easy to add new providers and models
5. **Optimized for scale**: Round-robin and rate limiting built-in

## Next Steps

1. Implement model field resolution logic
2. Add model pool configuration support
3. Update test suite for new model handling
4. Build army coordination layer
5. Add free model rate limiting

This design provides a complete, extensible foundation for multi-model execution while maintaining full backward compatibility.
