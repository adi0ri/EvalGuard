"""Source-controlled, immutable agent-instruction prompt definitions."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from evalguard.db.models import AgentPrompt
from evalguard.evaluation.suite import SuiteDefinitionConflictError, definition_hash

PROMPT_NAME = "parceldesk-support-agent"
PROMPT_V1_VERSION = "1.0.0"
PROMPT_V2_VERSION = "2.0.0"
DEFAULT_PROMPT_VERSION = PROMPT_V2_VERSION


@dataclass(frozen=True)
class AgentPromptDefinition:
    name: str
    version: str
    description: str
    instructions: str
    definition_hash: str


def _definition(
    *,
    version: str,
    description: str,
    instructions: str,
) -> AgentPromptDefinition:
    payload = {
        "name": PROMPT_NAME,
        "version": version,
        "instructions": instructions,
    }
    return AgentPromptDefinition(
        name=PROMPT_NAME,
        version=version,
        description=description,
        instructions=instructions,
        definition_hash=definition_hash(payload),
    )


PROMPT_V1 = _definition(
    version=PROMPT_V1_VERSION,
    description="Concise ParcelDesk tool-use instructions for the Phase 5 baseline.",
    instructions=(
        "Resolve the ParcelDesk customer's request with the available typed tools. "
        "Use persisted tool results as the source of truth and return one structured resolution."
    ),
)

PROMPT_V2 = _definition(
    version=PROMPT_V2_VERSION,
    description="Policy-explicit ParcelDesk instructions for the Phase 5 candidate.",
    instructions=(
        "Resolve the ParcelDesk customer's request with the available typed tools. "
        "First verify the requested order through the tools and act only within the trusted "
        "requester's authorization boundary. Enforce refund, fulfillment, and escalation policy; "
        "never claim an action succeeded unless the persisted tool result confirms it. Avoid "
        "disclosing protected customer or order data, do not repeat state-changing intent, and "
        "return one structured resolution grounded in the recorded outcome."
    ),
)

AGENT_PROMPT_DEFINITIONS = (PROMPT_V1, PROMPT_V2)
AGENT_PROMPT_BY_VERSION = {
    definition.version: definition for definition in AGENT_PROMPT_DEFINITIONS
}


def seed_agent_prompts(session: Session) -> tuple[AgentPrompt, ...]:
    """Insert or exactly verify the two published Phase 5 prompt variants."""

    persisted: list[AgentPrompt] = []
    for definition in AGENT_PROMPT_DEFINITIONS:
        prompt = session.scalar(
            select(AgentPrompt)
            .where(
                AgentPrompt.name == definition.name,
                AgentPrompt.version == definition.version,
            )
            .with_for_update()
        )
        if prompt is None:
            prompt = AgentPrompt(
                name=definition.name,
                version=definition.version,
                description=definition.description,
                instructions=definition.instructions,
                definition_hash=definition.definition_hash,
                is_published=False,
            )
            session.add(prompt)
            session.flush()
        elif (
            prompt.description != definition.description
            or prompt.instructions != definition.instructions
            or prompt.definition_hash != definition.definition_hash
        ):
            raise SuiteDefinitionConflictError(
                f"Stored agent prompt {definition.name} {definition.version} differs from source"
            )

        if not prompt.is_published:
            prompt.is_published = True
            session.flush()
        persisted.append(prompt)

    return tuple(persisted)


def validate_agent_prompt(prompt: AgentPrompt) -> AgentPromptDefinition:
    """Return the exact source definition or reject unsupported/drifted prompt state."""

    definition = AGENT_PROMPT_BY_VERSION.get(prompt.version)
    if (
        definition is None
        or prompt.name != definition.name
        or prompt.description != definition.description
        or prompt.instructions != definition.instructions
        or prompt.definition_hash != definition.definition_hash
        or not prompt.is_published
    ):
        raise SuiteDefinitionConflictError(
            f"Persisted agent prompt {prompt.name} {prompt.version} differs from source"
        )
    return definition
