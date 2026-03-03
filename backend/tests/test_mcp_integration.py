import asyncio
import pytest
from backend.app.mcp.manager import MCPManager, MCPServerConfig
from backend.app.agent.tools import ToolRegistry, build_registry
from backend.app.agent.tools.definitions import get_pydantic_definitions

@pytest.mark.asyncio
async def test_mcp_pydantic_generation():
    manager = MCPManager()
    registry = ToolRegistry()
    
    config = MCPServerConfig(
        name="test_everything",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-everything"]
    )
    
    await manager.add_server(config)
    await manager.refresh_tools(registry)
    
    # Check if we can generate Pydantic models
    tool_names = [n for n in registry.registered_names if n.startswith("mcp_test_everything_")]
    models = manager.get_pydantic_models(tool_names)
    
    assert len(models) > 0
    # Verify a model (e.g., Echo)
    echo_model = next((m for m in models if "Echo" in m.__name__), None)
    assert echo_model is not None
    assert "message" in echo_model.model_fields
    
    # Test the global resolver
    from backend.app.mcp import mcp_manager
    # We need to register the tools in the global manager for the resolver to work
    await mcp_manager.add_server(config)
    try:
        await mcp_manager.refresh_tools(build_registry())
        
        resolved = mcp_manager.resolve_tool_name(echo_model.__name__)
        assert resolved.startswith("mcp_test_everything_")
    finally:
        await manager.stop_all()
        await mcp_manager.stop_all()

@pytest.mark.asyncio
async def test_mcp_manager_everything():
    manager = MCPManager()
    registry = ToolRegistry()
    
    # Start the "everything" server
    # Note: Requires npx/node to be available in the test environment
    config = MCPServerConfig(
        name="test_everything",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-everything"]
    )
    
    try:
        await manager.add_server(config)
        await manager.refresh_tools(registry)
        
        # Verify tools are registered
        names = registry.registered_names
        mcp_tools = [n for n in names if n.startswith("mcp_test_everything_")]
        assert len(mcp_tools) > 0
        
        # Test calling a tool (everything server has a 'echo' tool usually, 
        # but let's check what it actually has)
        echo_tool = next((n for n in mcp_tools if "echo" in n), None)
        if echo_tool:
            result = await registry.execute(echo_tool, {"message": "hello"}, {})
            assert "hello" in result.get("result", "")
            
    finally:
        await manager.stop_all()

if __name__ == "__main__":
    asyncio.run(test_mcp_manager_everything())
