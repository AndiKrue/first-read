"""ADK 2.x agent graph for the FIRST READ pipeline."""

from google.adk.agents import LlmAgent
from google.adk.tools import AgentTool, FunctionTool
from pydantic import BaseModel, Field

from first_read.memory import get_mcp_toolset
from first_read.models import REASONING_MODEL
from first_read.tools.assemble import assemble_tool
from first_read.tools.breakdown import breakdown_tool
from first_read.tools.story import generate_scene
from first_read.tools.storyboard import storyboard_tool
from first_read.tools.tableread import tableread_tool


class PipelineState(BaseModel):
    run_id: str = ""
    breakdown: dict = Field(default_factory=dict)
    asset_urls: list[str] = Field(default_factory=list)


story_agent = LlmAgent(
    name="story_stage",
    model=REASONING_MODEL,
    description="Write an original, validated single-scene Fountain script.",
    instruction=(
        "Call generate_scene exactly once with the supplied story spec. Return the "
        "complete Fountain text unchanged so it can be parsed by the next stage."
    ),
    tools=[FunctionTool(func=generate_scene)],
)


breakdown_agent = LlmAgent(
    name="breakdown_stage",
    model=REASONING_MODEL,
    description="Consult production memory, then produce a validated scene breakdown.",
    instruction=(
        "Call breakdown_tool exactly once. Return its complete result without "
        "inventing or dropping fields."
    ),
    tools=[FunctionTool(func=breakdown_tool)],
)

storyboard_agent = LlmAgent(
    name="storyboard_stage",
    model=REASONING_MODEL,
    description="Render every visual beat as a reference-conditioned panel.",
    instruction=(
        "Call storyboard_tool with the exact scene and breakdown from the prior "
        "stage. Preserve the ordered panel results."
    ),
    tools=[FunctionTool(func=storyboard_tool)],
)

tableread_agent = LlmAgent(
    name="table_read_stage",
    model=REASONING_MODEL,
    description="Cast sticky voices and perform the scene as one table read.",
    instruction=(
        "Call tableread_tool with the unchanged scene and breakdown. Preserve its "
        "measured duration and audio location."
    ),
    tools=[FunctionTool(func=tableread_tool)],
)

assembly_agent = LlmAgent(
    name="assembly_stage",
    model=REASONING_MODEL,
    description="Mux the ordered panel files and measured table read into an MP4.",
    instruction=(
        "Call assemble_tool only after panels and audio exist. Pass every prior "
        "stage result unchanged."
    ),
    tools=[FunctionTool(func=assemble_tool)],
)

root_agent = LlmAgent(
    name="first_read",
    model=REASONING_MODEL,
    description="Write or accept one Fountain scene, then make a performed animatic.",
    state_schema=PipelineState,
    instruction=(
        "When the caller supplies a story spec instead of a scene, run story first "
        "and use its Fountain output as the unchanged input to breakdown. When the "
        "caller supplies a scene, skip story. Then run the pipeline in strict order: "
        "breakdown, storyboard, table read, assembly. Each stage consumes the exact "
        "output of the stage before it. "
        "Keep run_id, the full breakdown, and each produced asset URL in session "
        "state. Never assemble before both panels and measured audio exist. Use "
        "the ClickHouse MCP tools for read-only production-memory questions."
    ),
    tools=[
        AgentTool(story_agent),
        AgentTool(breakdown_agent),
        AgentTool(storyboard_agent),
        AgentTool(tableread_agent),
        AgentTool(assembly_agent),
        get_mcp_toolset(),
    ],
)
