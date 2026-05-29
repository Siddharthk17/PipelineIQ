"""WebAssembly UDF compute step — wasm_compute.

Executes a user-supplied Wasm function against every row of an input
DataFrame. The Wasm module runs inside a Wasmtime sandbox with no
filesystem, network, or system-call access, enforced by an empty
Linker and per-row CPU fuel budget.

YAML format:

    - name: custom_risk_score
      type: wasm_compute
      input: customer_data
      wasm_file_id: "module-uuid"
      function: "compute_risk"
      input_columns: [age, income, credit_score, payment_history]
      output_column: risk_score
"""

from dataclasses import dataclass, field


@dataclass
class WasmComputeStep:
    """Configuration for a single wasm_compute pipeline step.

    This mirrors WasmComputeStepConfig from the parser but lives as
    a standalone definition so the step type can be documented and
    referenced independently.
    """

    name: str
    step_type: str = "wasm_compute"
    input: str = ""
    wasm_file_id: str = ""
    function: str = ""
    input_columns: list[str] = field(default_factory=list)
    output_column: str = ""
    contract: dict | None = None
