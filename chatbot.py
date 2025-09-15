import os
import asyncio
from rich.console import Console
from rich.panel import Panel
from rich.columns import Columns
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Prompt, Confirm
from rich.syntax import Syntax
from rich.markdown import Markdown
from datetime import datetime

import json
import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from contextlib import AsyncExitStack
from dotenv import load_dotenv
import re
import aiohttp

import anthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

load_dotenv()

@dataclass
class MCPInteraction:
    timestamp: str
    server_name: str
    request_type: str
    request_data: Dict[Any, Any]
    response_data: Dict[Any, Any]
    status: str

@dataclass
class ExternalMCPServer:
    name: str
    description: str
    command: str
    args: List[str]
    working_directory: Optional[str] = None
    env_vars: Optional[Dict[str, str]] = None
    repository_url: Optional[str] = None
    author: Optional[str] = None

class MCPLogger:    
    def __init__(self, log_file: str = "mcp_interactions.log"):
        self.log_file = log_file
        self.interactions: List[MCPInteraction] = []
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
    
    def log_interaction(self, server_name: str, request_type: str, 
                       request_data: Dict[Any, Any], response_data: Dict[Any, Any], 
                       status: str = "success"):
        interaction = MCPInteraction(
            timestamp=datetime.now().isoformat(),
            server_name=server_name,
            request_type=request_type,
            request_data=request_data,
            response_data=response_data,
            status=status
        )
        
        self.interactions.append(interaction)
        
        log_message = f"MCP Interaction - Server: {server_name}, Type: {request_type}, Status: {status}"
        self.logger.info(log_message)

class RemoteMCPClient:
    
    def __init__(self, base_url: str, server_name: str = "remote_system_monitor"):
        self.base_url = base_url.rstrip('/')
        self.server_name = server_name
        self.tools = []
        self.session = None
    
    async def initialize(self):
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                
                try:
                    async with session.get(f"{self.base_url}/health") as response:
                        if response.status != 200:
                            return False
                except Exception:
                    return False
                
                
                try:
                    async with session.get(f"{self.base_url}/mcp/tools/list") as response:
                        if response.status == 200:
                            data = await response.json()
                            self.tools = data.get('tools', [])
                        else:
                            return False
                except Exception:
                    return False
            
            return True
        except Exception:
            return False
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        try:
            payload = {
                "name": tool_name,
                "arguments": arguments,
                "id": f"req_{datetime.now().timestamp()}"
            }
            
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{self.base_url}/mcp/tools/call",
                    json=payload,
                    headers={'Content-Type': 'application/json'}
                ) as response:
                    
                    if response.status == 200:
                        result = await response.json()
                        
                        
                        if 'result' in result:
                            tool_result = result['result']
                            return {
                                "content": tool_result.get('content', []),
                                "isError": tool_result.get('isError', False),
                                "server_type": "remote",
                                "author": "Remote Server"
                            }
                        elif 'error' in result:
                            error_msg = result['error'].get('message', 'Unknown error')
                            return {
                                "content": [{"type": "text", "text": f"Remote server error: {error_msg}"}],
                                "isError": True,
                                "server_type": "remote",
                                "author": "Remote Server"
                            }
                        else:
                            return {
                                "content": [{"type": "text", "text": f"Unexpected response format: {result}"}],
                                "isError": True,
                                "server_type": "remote",
                                "author": "Remote Server"
                            }
                    else:
                        response_text = await response.text()
                        return {
                            "content": [{"type": "text", "text": f"HTTP Error {response.status}: {response_text}"}],
                            "isError": True,
                            "server_type": "remote",
                            "author": "Remote Server"
                        }
        
        except asyncio.TimeoutError:
            return {
                "content": [{"type": "text", "text": "Remote server timeout - please try again"}],
                "isError": True,
                "server_type": "remote",
                "author": "Remote Server"
            }
        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Connection error: {str(e)}"}],
                "isError": True,
                "server_type": "remote",
                "author": "Remote Server"
            }
    
    def get_tools_info(self) -> List[Dict[str, Any]]:
        tools_with_metadata = []
        for tool in self.tools:
            tool_with_metadata = tool.copy()
            tool_with_metadata.update({
                "server_type": "remote",
                "author": "Remote Server",
                "repository_url": "N/A"
            })
            tools_with_metadata.append(tool_with_metadata)
        return tools_with_metadata

class MCPServerManager:
    def __init__(self, logger: MCPLogger):
        self.logger = logger
        self.servers: Dict[str, Dict[str, Any]] = {}
        self.exit_stack = AsyncExitStack()
        self.external_servers: List[ExternalMCPServer] = []
        self.remote_clients: Dict[str, RemoteMCPClient] = {}
        self.config = self._load_server_config()
    
    def _load_server_config(self) -> Dict[str, Any]:
        config = {
            'timeout': int(os.getenv('MCP_TIMEOUT', '30')),
            'workspace_dir': os.getenv('MCP_WORKSPACE_DIR', './mcp_workspace'),
            'csv_server_path': os.getenv('CSV_SERVER_PATH', './csv_mcp_server.py'),
            'filesystem_path': os.getenv('FILESYSTEM_PATH', './mcp_workspace'),
            'git_path': os.getenv('GIT_PATH', './mcp_workspace'),
            'remote_server_url': os.getenv('REMOTE_MCP_URL'),
        }
        return config
    
    async def add_remote_server(self, server_url: str, server_name: str = "remote_system_monitor"):
        try:
            remote_client = RemoteMCPClient(server_url, server_name)
            
            if await remote_client.initialize():
                self.remote_clients[server_name] = remote_client
                
                
                self.servers[server_name] = {
                    "session": None,
                    "tools": [{"name": tool["name"], "description": tool["description"]} for tool in remote_client.tools],
                    "status": "connected",
                    "type": "remote",
                    "base_url": server_url,
                    "author": "Remote Server"
                }
                
                self.logger.log_interaction(
                    server_name, 
                    "remote_server_connection",
                    {"base_url": server_url, "server_type": "remote"},
                    {"tools": self.servers[server_name]["tools"], "status": "connected", "server_type": "remote"},
                    "success"
                )
                
                return True
            else:
                return False
                
        except Exception as e:
            self.logger.log_interaction(
                server_name, 
                "remote_server_connection",
                {"base_url": server_url, "server_type": "remote"},
                {"error": str(e)},
                "error"
            )
            return False
    
    async def add_filesystem_server(self, allowed_path: str = None):
        if allowed_path is None:
            allowed_path = self.config['filesystem_path']
        
        os.makedirs(allowed_path, exist_ok=True)
            
        try:
            server_params = StdioServerParameters(
                command="npx",
                args=["-y", "@modelcontextprotocol/server-filesystem", allowed_path],
                env=None
            )
            
            stdio_transport = await self.exit_stack.enter_async_context(
                stdio_client(server_params)
            )
            stdio, write = stdio_transport
            session = await self.exit_stack.enter_async_context(
                ClientSession(stdio, write)
            )
            
            await asyncio.wait_for(session.initialize(), timeout=self.config['timeout'])
            
            tools_response = await session.list_tools()
            tools = [{"name": tool.name, "description": tool.description} for tool in tools_response.tools]
            
            self.servers["filesystem"] = {
                "session": session,
                "tools": tools,
                "status": "connected",
                "allowed_path": allowed_path,
                "type": "official"
            }
            
            self.logger.log_interaction(
                "filesystem", 
                "server_connection",
                {"allowed_path": allowed_path},
                {"tools": tools, "status": "connected"}
            )
            
        except Exception as e:
            self.logger.log_interaction(
                "filesystem", 
                "server_connection",
                {"allowed_path": allowed_path},
                {"error": str(e)},
                "error"
            )
    
    async def add_git_server(self, repository_path: str = None):
        if repository_path is None:
            repository_path = self.config['git_path']
        
        os.makedirs(repository_path, exist_ok=True)
            
        try:
            server_params = StdioServerParameters(
                command="uvx",
                args=["mcp-server-git"],
                env={"PWD": repository_path}
            )
            
            stdio_transport = await self.exit_stack.enter_async_context(
                stdio_client(server_params)
            )
            stdio, write = stdio_transport
            session = await self.exit_stack.enter_async_context(
                ClientSession(stdio, write)
            )
            
            await asyncio.wait_for(session.initialize(), timeout=self.config['timeout'])
            
            tools_response = await session.list_tools()
            tools = [{"name": tool.name, "description": tool.description} for tool in tools_response.tools]
            
            self.servers["git"] = {
                "session": session,
                "tools": tools,
                "status": "connected",
                "repository_path": repository_path,
                "type": "official"
            }
            
            self.logger.log_interaction(
                "git", 
                "server_connection",
                {"repository_path": repository_path},
                {"tools": tools, "status": "connected"}
            )
            
        except Exception as e:
            self.logger.log_interaction(
                "git", 
                "server_connection",
                {"repository_path": repository_path},
                {"error": str(e)},
                "error"
            )
    
    async def add_csv_analysis_server(self, server_path: str = None):
        if server_path is None:
            server_path = self.config['csv_server_path']
            
        possible_paths = [
            server_path,
            "./csv_mcp_server.py",
            "./server/csv_mcp_server.py",
            "../csv_mcp_server.py"
        ]
        
        actual_path = None
        for path in possible_paths:
            if os.path.exists(path):
                actual_path = path
                break
        
        if not actual_path:
            return
            
        try:
            workspace = os.path.abspath(self.config['workspace_dir'])
            os.makedirs(workspace, exist_ok=True)
            
            env_vars = os.environ.copy()
            env_vars["MCP_WORKSPACE_DIR"] = workspace
            
            server_params = StdioServerParameters(
                command="python",
                args=[actual_path],
                env=env_vars
            )
            
            stdio_transport = await self.exit_stack.enter_async_context(
                stdio_client(server_params)
            )
            stdio, write = stdio_transport
            session = await self.exit_stack.enter_async_context(
                ClientSession(stdio, write)
            )
            
            await asyncio.wait_for(session.initialize(), timeout=self.config['timeout'])
            
            tools_response = await session.list_tools()
            tools = [{"name": tool.name, "description": tool.description} for tool in tools_response.tools]
            
            self.servers["csv_analysis"] = {
                "session": session,
                "tools": tools,
                "status": "connected",
                "server_path": actual_path,
                "type": "own"
            }
            
            self.logger.log_interaction(
                "csv_analysis", 
                "server_connection",
                {"server_path": actual_path, "workspace": workspace},
                {"tools": tools, "status": "connected"}
            )
            
        except Exception as e:
            self.logger.log_interaction(
                "csv_analysis", 
                "server_connection",
                {"server_path": server_path},
                {"error": str(e)},
                "error"
            )
    
    def register_external_server(self, external_server: ExternalMCPServer):
        self.external_servers.append(external_server)
    
    async def add_external_server(self, server_name: str) -> bool:
        external_server = next(
            (server for server in self.external_servers if server.name == server_name), 
            None
        )
        
        if not external_server:
            return False
        
        try:
            original_cwd = os.getcwd()
            if external_server.working_directory:
                if os.path.exists(external_server.working_directory):
                    os.chdir(external_server.working_directory)
            
            env = os.environ.copy()
            if external_server.env_vars:
                env.update(external_server.env_vars)
            
            server_params = StdioServerParameters(
                command=external_server.command,
                args=external_server.args,
                env=env
            )
            
            stdio_transport = await self.exit_stack.enter_async_context(
                stdio_client(server_params)
            )
            stdio, write = stdio_transport
            session = await self.exit_stack.enter_async_context(
                ClientSession(stdio, write)
            )
            
            await asyncio.wait_for(session.initialize(), timeout=self.config['timeout'])
            os.chdir(original_cwd)
            
            tools_response = await session.list_tools()
            tools = [{"name": tool.name, "description": tool.description} for tool in tools_response.tools]
            
            self.servers[server_name] = {
                "session": session,
                "tools": tools,
                "status": "connected",
                "type": "external",
                "author": external_server.author,
                "description": external_server.description,
                "repository_url": external_server.repository_url
            }
            
            self.logger.log_interaction(
                server_name, 
                "external_server_connection",
                {
                    "author": external_server.author,
                    "command": external_server.command,
                    "args": external_server.args
                },
                {"tools": tools, "status": "connected"}
            )
            return True
            
        except Exception as e:
            os.chdir(original_cwd)  
            self.logger.log_interaction(
                server_name, 
                "external_server_connection",
                {"author": external_server.author},
                {"error": str(e)},
                "error"
            )
            return False
    
    async def connect_external_servers(self, server_names: List[str] = None):
        if server_names is None:
            server_names = [server.name for server in self.external_servers]
        
        successful_connections = []
        failed_connections = []
        
        for server_name in server_names:
            if await self.add_external_server(server_name):
                successful_connections.append(server_name)
            else:
                failed_connections.append(server_name)
        
        return successful_connections, failed_connections
    
    async def call_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if server_name in self.remote_clients:
            try:
                result = await self.remote_clients[server_name].call_tool(tool_name, arguments)
                
                self.logger.log_interaction(
                    server_name,
                    f"tool_call:{tool_name}",
                    arguments,
                    result,
                    "success" if not result.get("isError", False) else "error"
                )
                
                return result
                
            except Exception as e:
                error_response = {
                    "error": str(e), 
                    "isError": True,
                    "server_type": "remote",
                    "content": [{
                        "type": "text",
                        "text": f"Error calling remote tool: {str(e)}"
                    }]
                }
                
                self.logger.log_interaction(
                    server_name,
                    f"tool_call:{tool_name}",
                    arguments,
                    error_response,
                    "error"
                )
                
                return error_response
        
        if server_name not in self.servers:
            raise ValueError(f"Server {server_name} not connected")
        
        server = self.servers[server_name]
        if server["status"] != "connected":
            raise ValueError(f"Server {server_name} not active")
        
        try:
            session = server["session"]
            
            result = await asyncio.wait_for(
                session.call_tool(tool_name, arguments), 
                timeout=self.config['timeout']
            )
            
            response_data = {
                "content": [],
                "isError": False,
                "server_type": server.get("type", "unknown"),
                "author": server.get("author", "N/A")
            }
            
            if hasattr(result, 'content') and result.content:
                for content in result.content:
                    if hasattr(content, 'text'):
                        response_data["content"].append({
                            "type": "text", 
                            "text": content.text
                        })
                    elif hasattr(content, 'type'):
                        response_data["content"].append({
                            "type": content.type,
                            "text": str(content)
                        })
                    else:
                        response_data["content"].append({
                            "type": "text",
                            "text": str(content)
                        })
            
            if hasattr(result, 'isError') and result.isError:
                response_data["isError"] = True
            
            if not response_data["content"]:
                response_data["content"] = [{
                    "type": "text",
                    "text": f"Tool {tool_name} executed successfully"
                }]
            
            self.logger.log_interaction(
                server_name,
                f"tool_call:{tool_name}",
                arguments,
                response_data,
                "error" if response_data["isError"] else "success"
            )
            
            return response_data
        
        except asyncio.TimeoutError:
            error_response = {
                "error": "Timeout: Tool took too long to respond", 
                "isError": True,
                "server_type": server.get("type", "unknown"),
                "content": [{
                    "type": "text", 
                    "text": "Timeout: Tool took too long to respond"
                }]
            }
            self.logger.log_interaction(
                server_name,
                f"tool_call:{tool_name}",
                arguments,
                error_response,
                "timeout"
            )
            return error_response
        
        except Exception as e:
            error_response = {
                "error": str(e), 
                "isError": True,
                "server_type": server.get("type", "unknown"),
                "content": [{
                    "type": "text",
                    "text": f"Error executing tool: {str(e)}"
                }]
            }
            self.logger.log_interaction(
                server_name,
                f"tool_call:{tool_name}",
                arguments,
                error_response,
                "error"
            )
            return error_response
    
    async def validate_server_connection(self, server_name: str) -> bool:
        if server_name not in self.servers and server_name not in self.remote_clients:
            return False
        
        if server_name in self.remote_clients:
            try:
                result = await self.remote_clients[server_name].call_tool("system_monitor_info", {})
                return not result.get("isError", True)
            except:
                return False
        
        server = self.servers[server_name]
        if server["status"] != "connected":
            return False
        
        try:
            if server_name == "csv_analysis":
                result = await self.call_tool(server_name, "debug_workspace", {})
                return not result.get("isError", True)
            elif server_name == "filesystem":
                workspace = self.config['filesystem_path']
                test_path = os.path.join(workspace, "connection_test.txt")
                result = await self.call_tool(server_name, "write_file", {
                    "path": test_path,
                    "content": "connection test"
                })
                return not result.get("isError", True)
            return True
        except:
            return False
    
    def get_all_tools(self) -> Dict[str, List[Dict[str, Any]]]:
        all_tools = {}
        
        for server_name, server_info in self.servers.items():
            if server_info["status"] == "connected":
                tools_with_metadata = []
                for tool in server_info["tools"]:
                    tool_with_metadata = tool.copy()
                    tool_with_metadata.update({
                        "server_type": server_info.get("type", "unknown"),
                        "author": server_info.get("author", "N/A"),
                        "repository_url": server_info.get("repository_url", "N/A")
                    })
                    tools_with_metadata.append(tool_with_metadata)
                
                all_tools[server_name] = tools_with_metadata
        
        for server_name, remote_client in self.remote_clients.items():
            if server_name not in all_tools:
                all_tools[server_name] = remote_client.get_tools_info()
        
        return all_tools
    
    def get_external_servers_summary(self) -> str:
        external_servers = {name: info for name, info in self.servers.items() 
                           if info.get("type") == "external"}
        
        remote_servers = {name: {
            "author": "Remote Server",
            "description": f"Remote MCP server at {client.base_url}",
            "repository_url": "N/A",
            "tools": client.tools,
            "type": "remote"
        } for name, client in self.remote_clients.items()}
        
        all_external = {**external_servers, **remote_servers}
        
        if not all_external:
            return "No external servers connected."
        
        summary = f"External servers connected ({len(all_external)}):\n"
        summary += "=" * 50 + "\n"
        
        for server_name, server_info in all_external.items():
            summary += f"\n{server_name}\n"
            summary += f"   Author: {server_info.get('author', 'N/A')}\n"
            summary += f"   Description: {server_info.get('description', 'N/A')}\n"
            summary += f"   Repository: {server_info.get('repository_url', 'N/A')}\n"
            summary += f"   Tools: {len(server_info.get('tools', []))}\n"
            
            for tool in server_info.get('tools', []):
                tool_name = tool.get('name', 'unnamed') if isinstance(tool, dict) else getattr(tool, 'name', 'unnamed')
                tool_desc = tool.get('description', 'No description') if isinstance(tool, dict) else getattr(tool, 'description', 'No description')
                summary += f"      • {tool_name}: {tool_desc}\n"
        
        return summary
    
    async def cleanup(self):
        await self.exit_stack.aclose()

class ConversationContext:    
    def __init__(self, max_messages: int = None):
        self.max_messages = max_messages or int(os.getenv('MAX_MESSAGES', '50'))
        self.messages: List[Dict[str, str]] = []
        self.conversation_start = datetime.now()
        self.system_message = None
    
    def add_message(self, role: str, content: str):
        if role == "system":
            self.system_message = content
            return
            
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        }
        
        self.messages.append(message)
        
        if len(self.messages) > self.max_messages:
            self.messages = self.messages[-self.max_messages:]
    
    def get_messages_for_api(self) -> tuple:
        conversation_messages = [{"role": msg["role"], "content": msg["content"]} for msg in self.messages]
        return self.system_message, conversation_messages
    
    def get_conversation_summary(self) -> str:
        total_messages = len(self.messages)
        user_messages = len([msg for msg in self.messages if msg["role"] == "user"])
        assistant_messages = len([msg for msg in self.messages if msg["role"] == "assistant"])
        
        duration = datetime.now() - self.conversation_start
        
        return f"""
Conversation summary:
- Started: {self.conversation_start.strftime("%Y-%m-%d %H:%M:%S")}
- Duration: {duration}
- Total messages: {total_messages}
- User messages: {user_messages}
- Assistant messages: {assistant_messages}
        """.strip()

class AnthropicLLMClient:    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("Anthropic API key required.")
        
        self.client = anthropic.Anthropic(api_key=self.api_key)
        self.model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
    
    def send_message(self, system_message: str, messages: List[Dict[str, str]], max_tokens: int = None) -> str:
        if max_tokens is None:
            max_tokens = int(os.getenv('MAX_TOKENS', '2048'))
            
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system_message,  
                messages=messages
            )
            return response.content[0].text
        except Exception as e:
            return f"Error communicating with Claude: {str(e)}"

class IntelligentToolExecutor:
    
    def __init__(self, server_manager: MCPServerManager, llm_client: AnthropicLLMClient):
        self.server_manager = server_manager
        self.llm_client = llm_client
    
    def get_tools_for_llm(self) -> str:
        all_tools = self.server_manager.get_all_tools()
        
        tools_description = "AVAILABLE MCP TOOLS:\n\n"
        
        for server_name, tools in all_tools.items():
            server_info = self.server_manager.servers.get(server_name, {})
            server_type = server_info.get("type", "unknown")
            
            tools_description += f"SERVER: {server_name} (type: {server_type})\n"
            
            for tool in tools:
                tools_description += f"  - {tool['name']}: {tool['description']}\n"
            
            tools_description += "\n"
        
        for server_name, client in self.server_manager.remote_clients.items():
            tools_description += f"SERVER: {server_name} (type: remote)\n"
            for tool in client.tools:
                tools_description += f"  - {tool['name']}: {tool['description']}\n"
            tools_description += "\n"
        
        return tools_description
    
    async def analyze_user_request(self, user_input: str) -> Dict[str, Any]:
        try:
            tools_info = self.get_tools_for_llm()
            
            workspace_path = self.server_manager.config.get('filesystem_path', './mcp_workspace')
            git_path = self.server_manager.config.get('git_path', './mcp_workspace')
            
            analysis_prompt = f"""You are an intelligent tool selector for an MCP chatbot system. 

{tools_info}

WORKSPACE FILES AVAILABLE:
- estudiantes.csv (academic student data)
- ventas.csv (commercial sales data)  
- simple.csv (simple test data)

IMPORTANT FILESYSTEM RULES:
- The filesystem server can ONLY access files within: {workspace_path}
- NEVER use relative paths like "." or ".." 
- ALWAYS use the full workspace path: {workspace_path}
- For listing files, ALWAYS use path: "{workspace_path}"

IMPORTANT GIT RULES:
- ALL Git tools require a "repo_path" parameter
- ALWAYS use repo_path: "{git_path}" for Git operations
- Git repository is located at: {git_path}

USER REQUEST: "{user_input}"

Analyze this request and determine:
1. Does the user need MCP tools? (yes/no)
2. If yes, which server and tool should be used?
3. What parameters should be passed to the tool?

SERVER SELECTION RULES:
- For system monitoring (CPU, memory, disk, network, processes): use remote_system_monitor server
- For CSV analysis: use csv_analysis server  
- For file operations (create, read, write, list files): use filesystem server
- For git operations: use git server
- If user mentions "servidor remoto" or "remote server": prefer remote servers

FILESYSTEM OPERATIONS - Use filesystem server for:
- "listar archivos" / "list files" → list_directory with path: "{workspace_path}"
- "crear archivo" / "create file" → write_file with path: "{workspace_path}/filename"
- "leer archivo" / "read file" → read_text_file with full path
- "buscar archivos" / "search files" → search_files with path: "{workspace_path}"

GIT OPERATIONS - Use git server for:
- "status del repositorio" / "git status" → git_status with repo_path: "{git_path}"
- "commit" / "git commit" → git_commit with repo_path: "{git_path}" and message: "commit message"
- "add archivos" / "git add" → git_add with repo_path: "{git_path}" and files: ["file1", "file2"]
- "log de commits" / "git log" → git_log with repo_path: "{git_path}"
- "crear rama" / "create branch" → git_create_branch with repo_path: "{git_path}" and branch_name: "new_branch"

EXAMPLE RESPONSES:

For: "Lista los archivos en el directorio"
{{
"needs_tools": true,
"server_name": "filesystem",
"tool_name": "list_directory", 
"arguments": {{"path": "{workspace_path}"}},
"reasoning": "User wants to list files - use filesystem server with workspace path"
}}

For: "Muestra el status del repositorio git"
{{
"needs_tools": true,
"server_name": "git",
"tool_name": "git_status", 
"arguments": {{"repo_path": "{git_path}"}},
"reasoning": "User wants git status - use git server with repo_path"
}}

For: "Haz commit de los cambios con mensaje 'mi commit'"
{{
"needs_tools": true,
"server_name": "git",
"tool_name": "git_commit", 
"arguments": {{"repo_path": "{git_path}", "message": "mi commit"}},
"reasoning": "User wants to commit changes - use git server with repo_path and message"
}}

For: "Agrega el archivo test.txt al staging"
{{
"needs_tools": true,
"server_name": "git",
"tool_name": "git_add", 
"arguments": {{"repo_path": "{git_path}", "files": ["test.txt"]}},
"reasoning": "User wants to add file to git - use git server with repo_path and files"
}}

CRITICAL: 
- Always use full paths starting with {workspace_path} for filesystem operations!
- Always use repo_path: "{git_path}" for Git operations!

Respond with valid JSON only:
{{
"needs_tools": true/false,
"server_name": "server_name",
"tool_name": "tool_name", 
"arguments": {{}},
"reasoning": "explanation of your decision"
}}"""

            response = self.llm_client.send_message(
                "You are a precise tool selector. Respond only with valid JSON.",
                [{"role": "user", "content": analysis_prompt}]
            )
            
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                json_str = json_match.group()
                analysis = json.loads(json_str)
                return analysis
            else:
                return {"needs_tools": False, "reasoning": "Could not parse LLM response"}
                
        except json.JSONDecodeError as e:
            return {"needs_tools": False, "reasoning": f"JSON parse error: {str(e)}"}
        except Exception as e:
            return {"needs_tools": False, "reasoning": f"Error in analysis: {str(e)}"}
    
    async def execute_intelligent_tool_call(self, analysis: Dict[str, Any]) -> Optional[str]:
        try:
            if not analysis.get("needs_tools", False):
                return None
            
            server_name = analysis.get("server_name")
            tool_name = analysis.get("tool_name")
            arguments = analysis.get("arguments", {})
            
            if not server_name or not tool_name:
                return "Error: Could not determine which tool to use"
            
            if server_name == "git" and "repo_path" not in arguments:
                git_path = self.server_manager.config.get('git_path', './mcp_workspace')
                arguments["repo_path"] = git_path
            
            if server_name in self.server_manager.remote_clients and "csv" in tool_name.lower():
                arguments = await self._prepare_remote_arguments(tool_name, arguments)
            
            result = await self.server_manager.call_tool(server_name, tool_name, arguments)
            
            return self._extract_text_from_result(result)
            
        except Exception as e:
            return f"Error executing tool {tool_name} on {server_name}: {str(e)}"
    
    async def _prepare_remote_arguments(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if not arguments and "csv" in tool_name.lower():
            arguments = {"file_path": "estudiantes.csv"}
        
        if "csv" in tool_name.lower() and "file_path" in arguments:
            file_path = arguments["file_path"]
            workspace_dir = self.server_manager.config['workspace_dir']
            full_path = os.path.join(workspace_dir, file_path)
            
            if os.path.exists(full_path):
                try:
                    with open(full_path, 'r', encoding='utf-8') as f:
                        csv_content = f.read()
                    
                    remote_args = {
                        "csv_content": csv_content,
                        "file_name": file_path
                    }
                    
                    for key, value in arguments.items():
                        if key not in ["file_path"] and key in ["plot_type", "columns", "method", "filters", "operations", "group_by", "aggregations"]:
                            remote_args[key] = value
                    
                    return remote_args
                    
                except Exception:
                    return arguments
        
        return arguments
    
    def _extract_text_from_result(self, result: Dict[str, Any]) -> str:
        try:
            if result.get('isError', False):
                error_msg = result.get('error', 'Unknown error')
                return f"Error in MCP tool: {error_msg}"
            
            content = result.get('content', [])
            
            if not content:
                return "MCP tool completed successfully"
            
            text_parts = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get('text', '')
                    if text:
                        text_parts.append(str(text))
                    else:
                        text_parts.append(str(item))
                elif isinstance(item, str):
                    text_parts.append(item)
                else:
                    text_parts.append(str(item))
            
            if text_parts:
                return '\n'.join(text_parts)
            else:
                return "MCP tool completed successfully"
                
        except Exception as e:
            return f"Error processing MCP result: {str(e)}"

class MCPChatbot:
    def __init__(self, api_key: Optional[str] = None):
        self.llm_client = AnthropicLLMClient(api_key)
        self.context = ConversationContext()
        self.mcp_logger = MCPLogger()
        self.server_manager = MCPServerManager(self.mcp_logger)
        
        workspace_dir = self.server_manager.config['workspace_dir']
        system_message = f"""You are an intelligent assistant with access to MCP (Model Context Protocol) tools for enhanced capabilities.

Your main function is to help users with various tasks using the available MCP tools when appropriate.

AVAILABLE FILES in {workspace_dir}/:
- estudiantes.csv (academic student data)
- ventas.csv (commercial sales data)  
- simple.csv (simple test data)

CAPABILITIES:
- General conversation and knowledge questions
- System monitoring (CPU, memory, disk, network, processes)
- File system operations
- Git repository management  
- CSV data analysis
- Data visualization
- Statistical analysis

IMPORTANT: 
- When you receive results from MCP tools, interpret and present them clearly
- Maintain a professional and helpful tone
- Highlight the most important insights from analyses
- If no MCP tools are needed, respond normally with your knowledge

Workspace: {workspace_dir}"""
        
        self.context.add_message("system", system_message)
        self.intelligent_executor = None
    
    async def initialize_servers(self):
        workspace_dir = self.server_manager.config['workspace_dir']
        os.makedirs(workspace_dir, exist_ok=True)
        
        await self.server_manager.add_filesystem_server("./mcp_workspace")
        await self.server_manager.add_git_server("./mcp_workspace")
        await self.server_manager.add_csv_analysis_server()
        
        remote_url = self.server_manager.config['remote_server_url']
        if remote_url:
            await self.server_manager.add_remote_server(remote_url, "remote_system_monitor")
        
        if self.server_manager.external_servers:
            await self.server_manager.connect_external_servers()
        
        self.intelligent_executor = IntelligentToolExecutor(self.server_manager, self.llm_client)
    
    def process_special_command(self, user_input: str) -> bool:
        command = user_input.strip().lower()
        return command in ["/quit", "/tools", "/external", "/registered", "/log", "/context", "/clear", "/help"]
    
    async def cleanup(self):
        try:
            cleanup_task = asyncio.create_task(self.server_manager.cleanup())
            await asyncio.wait_for(cleanup_task, timeout=5.0)
        except:
            pass

class EnhancedTerminalUI:
    def __init__(self):
        self.console = Console()
        self.setup_colors()
    
    def setup_colors(self):
        self.colors = {
            'primary': 'bright_blue',
            'secondary': 'cyan',
            'success': 'bright_green',
            'warning': 'yellow',
            'error': 'bright_red',
            'neutral': 'white',
            'muted': 'bright_black',
            'accent': 'magenta'
        }
    
    def clear_screen(self):
        os.system('cls' if os.name == 'nt' else 'clear')
    
    def show_welcome_screen(self):
        self.clear_screen()
        
        welcome_text = """Welcome to the MCP Chatbot!
        """
        
        welcome_panel = Panel(
            Markdown(welcome_text),
            border_style=self.colors['primary'],
            padding=(1, 2),
            title="[bold bright_blue]MCP Chatbot[/bold bright_blue]"
        )
        
        self.console.print(welcome_panel)
        self.show_status_bar("System starting...", "info")
    
    def show_status_bar(self, status_text: str, status_type: str = "info"):
        color = self.colors.get(status_type, self.colors['neutral'])
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        status_panel = Panel(
            f"[{self.colors['muted']}]{timestamp}[/] [{color}]◉[/] {status_text}",
            border_style=self.colors['muted'],
            height=3
        )
        self.console.print(status_panel)
    
    def show_main_menu(self):
        menu_table = Table(show_header=False, box=None, padding=(0, 2))
        
        commands = [
            ("Chat", "Intelligent chat with MCP tools", "/chat"),
            ("Tools", "View available MCP tools", "/tools"),
            ("Servers", "Manage MCP servers", "/servers"),
            ("Log MCP", "View MCP interactions in real time", "/log"),
            ("Configuration", "System options", "/config"),
            ("Help", "Show available commands", "/help"),
            ("Exit", "Terminate application", "/quit")
        ]
        
        for icon_name, description, command in commands:
            menu_table.add_row(
                f"[{self.colors['accent']}]{icon_name}[/]",
                f"[{self.colors['neutral']}]{description}[/]",
                f"[{self.colors['muted']}]{command}[/]"
            )
        
        menu_panel = Panel(
            menu_table,
            title="[bold]Main Menu[/bold]",
            border_style=self.colors['primary']
        )
        
        self.console.print(menu_panel)
    
    def show_server_status(self, servers: dict, remote_clients: dict = None):
        status_table = Table(title="MCP Server Status")
        status_table.add_column("Server", style=self.colors['primary'])
        status_table.add_column("Status", justify="center")
        status_table.add_column("Tools", justify="center")
        status_table.add_column("Type", style=self.colors['secondary'])
        
        for server_name, server_info in servers.items():
            status = server_info.get('status', 'unknown')
            tools_count = len(server_info.get('tools', []))
            server_type = server_info.get('type', 'unknown')
            
            if status == 'connected':
                status_icon = f"[{self.colors['success']}]ACTIVE[/]"
            else:
                status_icon = f"[{self.colors['error']}]ERROR[/]"
            
            status_table.add_row(
                server_name,
                status_icon,
                str(tools_count),
                server_type.title()
            )
        
        if remote_clients:
            for client_name, client in remote_clients.items():
                if client_name not in servers:
                    status_table.add_row(
                        client_name,
                        f"[{self.colors['success']}]REMOTE[/]",
                        str(len(client.tools)),
                        "Remote"
                    )
        
        self.console.print(status_table)
        
    def show_tools_grid(self, all_tools: dict):
        panels = []
        
        for server_name, tools in all_tools.items():
            tool_list = []
            for tool in tools:
                tool_name = tool.get('name', 'unnamed') if isinstance(tool, dict) else str(tool)
                tool_list.append(f"• {tool_name}")
            
            server_type = tools[0].get('server_type', 'unknown') if tools and isinstance(tools[0], dict) else 'unknown'
            
            if server_type == "remote":
                border_color = self.colors['accent']
                title_prefix = "[Remote] "
            elif server_type in ["csv_analysis", "own"]:
                border_color = self.colors['success'] 
                title_prefix = "[Local] "
            else:
                border_color = self.colors['secondary']
                title_prefix = ""
            
            server_panel = Panel(
                "\n".join(tool_list),
                title=f"[bold]{title_prefix}{server_name}[/bold]",
                border_style=border_color,
                padding=(0, 1)
            )
            panels.append(server_panel)
        
        columns = Columns(panels, equal=True, expand=True)
        self.console.print(columns)
    
    def show_chat_interface(self):
        chat_panel = Panel(
            "[bold]Enhanced Chat Mode[/bold]\n\n"
            "[green]✓ Intelligent tool selection enabled[/green]\n"
            "[blue]✓ Local and remote MCP servers active[/blue]\n"
            "[cyan]✓ System monitoring available[/cyan]\n\n"
            "Special commands:\n"
            f"[{self.colors['muted']}]• /tools - View available MCP tools[/]\n"
            f"[{self.colors['muted']}]• /log - View MCP call history[/]\n"
            f"[{self.colors['muted']}]• /clear - Clear context[/]\n"
            f"[{self.colors['muted']}]• /menu - Return to main menu[/]",
            border_style=self.colors['accent'],
            title="Intelligent Chat Interface"
        )
        self.console.print(chat_panel)
    
    def display_message(self, role: str, content: str, timestamp: str = None):
        if not timestamp:
            timestamp = datetime.now().strftime("%H:%M:%S")
        
        if role == "user":
            color = self.colors['primary']
            icon = "User"
            title = "User"
        else:
            color = self.colors['success']
            icon = "Claude"
            title = "Claude [Enhanced MCP]"
        
        if len(content) > 100:
            if '```' in content or 'def ' in content or 'import ' in content:
                formatted_content = Syntax(content, "python", theme="monokai", background_color="default")
            else:
                formatted_content = Markdown(content)
        else:
            formatted_content = content
        
        message_panel = Panel(
            formatted_content,
            title=f"[{color}]{icon} {title}[/] [{self.colors['muted']}]{timestamp}[/]",
            border_style=color,
            padding=(0, 1)
        )
        
        self.console.print(message_panel)
    
    def show_interaction_log(self, interactions: list, limit: int = 10):
        log_table = Table(title=f"Last {limit} MCP Interactions - Enhanced Tool Execution")
        log_table.add_column("Time", style=self.colors['muted'])
        log_table.add_column("Server", style=self.colors['primary'])
        log_table.add_column("Tool", style=self.colors['secondary'])
        log_table.add_column("Status", justify="center")
        log_table.add_column("Type", style=self.colors['accent'])
        
        recent_interactions = interactions[-limit:] if interactions else []
        
        if not recent_interactions:
            no_interactions_panel = Panel(
                "[yellow]NO MCP INTERACTIONS YET[/yellow]\n\n"
                "[cyan]The enhanced system will automatically determine when to use MCP tools[/cyan]\n"
                "Try asking about:\n"
                "• System monitoring\n"
                "• CSV data analysis\n"
                "• File operations\n"
                "• Git commands",
                border_style="cyan",
                title="Enhanced Tool System"
            )
            self.console.print(no_interactions_panel)
            return
        
        tool_usage_count = {"local": 0, "remote": 0, "filesystem": 0, "git": 0}
        
        for interaction in recent_interactions:
            timestamp = interaction.timestamp.split('T')[1].split('.')[0]
            
            server_type = "local"
            if "remote" in interaction.server_name:
                server_type = "remote"
                tool_usage_count["remote"] += 1
            elif "filesystem" in interaction.server_name:
                server_type = "filesystem"
                tool_usage_count["filesystem"] += 1
            elif "git" in interaction.server_name:
                server_type = "git"
                tool_usage_count["git"] += 1
            else:
                tool_usage_count["local"] += 1
            
            if interaction.status == "success":
                status_display = f"[{self.colors['success']}]✓[/]"
            else:
                status_display = f"[{self.colors['error']}]✗[/]"
            
            if server_type == "remote":
                server_display = f"[bold magenta]{interaction.server_name}[/bold magenta]"
                tool_display = f"[bold magenta]{interaction.request_type}[/bold magenta]"
            else:
                server_display = interaction.server_name
                tool_display = interaction.request_type
            
            log_table.add_row(
                timestamp,
                server_display,
                tool_display,
                status_display,
                server_type.title()
            )
        
        self.console.print(log_table)
        
        summary_panel = Panel(
            f"[green]Local tools used: {tool_usage_count['local']}[/green]\n"
            f"[magenta]Remote tools used: {tool_usage_count['remote']}[/magenta]\n"
            f"[blue]Filesystem tools used: {tool_usage_count['filesystem']}[/blue]\n"
            f"[yellow]Git tools used: {tool_usage_count['git']}[/yellow]\n"
            f"[cyan]Total interactions: {len(recent_interactions)}[/cyan]\n"
            f"[bold]ENHANCED MCP SYSTEM ACTIVE[/bold]",
            title="Enhanced Tool Usage Summary",
            border_style="green"
        )
        self.console.print(summary_panel)
    
    def show_progress(self, description: str):
        progress = Progress(
            SpinnerColumn(),
            TextColumn(f"[progress.description]{description}"),
            console=self.console,
        )
        task = progress.add_task(description, total=None)
        return progress, task
    
    def get_user_input(self, prompt_text: str = "Input") -> str:
        return Prompt.ask(
            f"[{self.colors['accent']}]{prompt_text}[/]",
            console=self.console
        )
    
    def get_confirmation(self, question: str) -> bool:
        return Confirm.ask(
            f"[{self.colors['warning']}]{question}[/]",
            console=self.console
        )
    
    def show_error(self, error_message: str):
        error_panel = Panel(
            f"[{self.colors['error']}]Error:[/] {error_message}",
            border_style=self.colors['error'],
            title="Error"
        )
        self.console.print(error_panel)
    
    def show_success(self, success_message: str):
        success_panel = Panel(
            f"[{self.colors['success']}]Success:[/] {success_message}",
            border_style=self.colors['success'],
            title="Completed"
        )
        self.console.print(success_panel)
    
    def show_info(self, info_message: str):
        info_panel = Panel(
            f"[{self.colors['secondary']}]Info:[/] {info_message}",
            border_style=self.colors['secondary'],
            title="Information"
        )
        self.console.print(info_panel)

class EnhancedMCPChatbot(MCPChatbot):
    def __init__(self, api_key=None):
        super().__init__(api_key)
        self.ui = EnhancedTerminalUI()
        self.current_mode = "menu"
    
    async def test_remote_server_connection(self):
        try:
            if "remote_system_monitor" in self.server_manager.remote_clients:
                result = await self.server_manager.validate_server_connection("remote_system_monitor")
                return result
            return False
        except Exception:
            return False
    
    async def enhanced_chat_loop(self):
        self.ui.show_welcome_screen()
        
        progress, task = self.ui.show_progress("Initializing enhanced MCP system...")
        with progress:
            await self.initialize_servers()
            
            local_working = await self.server_manager.validate_server_connection("csv_analysis") if "csv_analysis" in self.server_manager.servers else False
            remote_working = await self.test_remote_server_connection()
            filesystem_working = await self.server_manager.validate_server_connection("filesystem") if "filesystem" in self.server_manager.servers else False
            
            status_messages = []
            if filesystem_working:
                status_messages.append("Filesystem server OK")
            else:
                status_messages.append("Filesystem server ISSUES")
                
            if local_working:
                status_messages.append("CSV server OK")
            if remote_working:
                status_messages.append("Remote server OK")
                
            if filesystem_working and (local_working or remote_working):
                self.ui.show_success(f"Servers initialized: {', '.join(status_messages)}")
            else:
                self.ui.show_error(f"Some servers have issues: {', '.join(status_messages)}")
        
        while True:
            try:
                if self.current_mode == "menu":
                    self.ui.show_main_menu()
                    self.ui.show_server_status(self.server_manager.servers, self.server_manager.remote_clients)
                    
                    user_input = self.ui.get_user_input("Select an option").strip()
                    
                    if user_input == "/chat":
                        self.current_mode = "chat"
                        self.ui.show_chat_interface()
                    elif user_input == "/tools":
                        self.show_tools_interface()
                    elif user_input == "/servers":
                        self.show_servers_interface()
                    elif user_input == "/log":
                        self.ui.show_interaction_log(self.mcp_logger.interactions)
                        input("\nPress Enter to continue...")
                    elif user_input == "/quit":
                        if self.ui.get_confirmation("Are you sure you want to exit?"):
                            break
                    else:
                        self.ui.show_error("Command not recognized. Use /help to see options.")
                
                elif self.current_mode == "chat":
                    user_input = self.ui.get_user_input("Message").strip()
                    
                    if user_input == "/menu":
                        self.current_mode = "menu"
                        continue
                    elif user_input == "/log":
                        self.ui.show_interaction_log(self.mcp_logger.interactions)
                        continue
                    elif user_input.startswith("/"):
                        if self.process_special_command(user_input):
                            continue
                    
                    self.ui.display_message("user", user_input)
                    
                    progress, task = self.ui.show_progress("Analyzing request and selecting tools...")
                    mcp_result = None
                    
                    try:
                        with progress:
                            analysis = await self.intelligent_executor.analyze_user_request(user_input)
                            
                            if analysis.get("needs_tools", False):
                                mcp_result = await self.intelligent_executor.execute_intelligent_tool_call(analysis)
                    except Exception as e:
                        mcp_result = f"Error in intelligent tool execution: {str(e)}"
                    
                    if mcp_result:
                        enhanced_input = f"""User asked: {user_input}

Result from MCP tool execution:
{mcp_result}

Interpret and present this result clearly and usefully for the user. Highlight the most important insights and present the information in an organized way."""
                        
                        self.context.add_message("user", enhanced_input)
                    else:
                        self.context.add_message("user", user_input)
                    
                    progress, task = self.ui.show_progress("Claude generating response...")
                    try:
                        with progress:
                            system_message, messages = self.context.get_messages_for_api()
                            response = self.llm_client.send_message(system_message, messages)
                    except Exception as e:
                        response = f"Error communicating with Claude: {str(e)}"
                    
                    self.context.add_message("assistant", response)
                    self.ui.display_message("assistant", response)
                    
            except KeyboardInterrupt:
                if self.ui.get_confirmation("\nDo you want to exit the program?"):
                    break
                continue
            except Exception as e:
                self.ui.show_error(f"Unexpected error: {str(e)}")
    
    def show_tools_interface(self):
        self.ui.clear_screen()
        all_tools = self.server_manager.get_all_tools()
        self.ui.show_tools_grid(all_tools)
        input("\nPress Enter to continue...")
    
    def show_servers_interface(self):
        self.ui.clear_screen()
        self.ui.show_server_status(self.server_manager.servers, self.server_manager.remote_clients)
        
        if self.server_manager.external_servers or self.server_manager.remote_clients:
            self.ui.console.print("\n" + self.server_manager.get_external_servers_summary())
        
        input("\nPress Enter to continue...")

async def enhanced_main():
    try:
        if not os.getenv("ANTHROPIC_API_KEY"):
            console = Console()
            console.print("[red]Anthropic API key not found.[/red]")
            console.print("[yellow]Configure your ANTHROPIC_API_KEY in the .env file[/yellow]")
            return
        
        chatbot = EnhancedMCPChatbot()
        await chatbot.enhanced_chat_loop()
        await chatbot.cleanup()
        
    except Exception as e:
        console = Console()
        console.print(f"[red]Error initializing enhanced chatbot: {str(e)}[/red]")

if __name__ == "__main__":
    asyncio.run(enhanced_main())