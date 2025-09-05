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

class MCPServerManager:
    def __init__(self, logger: MCPLogger):
        self.logger = logger
        self.servers: Dict[str, Dict[str, Any]] = {}
        self.exit_stack = AsyncExitStack()
        self.external_servers: List[ExternalMCPServer] = []
        self.config = self._load_server_config()
    
    def _load_server_config(self) -> Dict[str, Any]:
        config = {
            'timeout': int(os.getenv('MCP_TIMEOUT', '30')),
            'workspace_dir': os.getenv('MCP_WORKSPACE_DIR', './mcp_workspace'),
            'csv_server_path': os.getenv('CSV_SERVER_PATH', './csv_mcp_server.py'),
            'filesystem_path': os.getenv('FILESYSTEM_PATH', './mcp_workspace'),
            'git_path': os.getenv('GIT_PATH', './mcp_workspace'),
        }
        return config
    
    async def add_filesystem_server(self, allowed_path: str = None):
        if allowed_path is None:
            allowed_path = self.config['filesystem_path']
            
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
            raise FileNotFoundError(f"CSV server not found in any of: {possible_paths}")
            
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
            raise
    
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
            
            if hasattr(result, 'isError'):
                response_data["isError"] = result.isError
            
            if not response_data["content"]:
                response_data["content"] = [{
                    "type": "text",
                    "text": "Tool returned no content"
                }]
                response_data["isError"] = True
            
            self.logger.log_interaction(
                server_name,
                f"tool_call:{tool_name}",
                arguments,
                response_data
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
        if server_name not in self.servers:
            return False
        
        server = self.servers[server_name]
        if server["status"] != "connected":
            return False
        
        try:
            if server_name == "csv_analysis":
                result = await self.call_tool(server_name, "debug_workspace", {})
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
        return all_tools
    
    def get_external_servers_summary(self) -> str:
        external_servers = {name: info for name, info in self.servers.items() 
                           if info.get("type") == "external"}
        
        if not external_servers:
            return "No external servers connected."
        
        summary = f"External servers connected ({len(external_servers)}):\n"
        summary += "=" * 50 + "\n"
        
        for server_name, server_info in external_servers.items():
            summary += f"\n{server_name}\n"
            summary += f"   Author: {server_info.get('author', 'N/A')}\n"
            summary += f"   Description: {server_info.get('description', 'N/A')}\n"
            summary += f"   Repository: {server_info.get('repository_url', 'N/A')}\n"
            summary += f"   Tools: {len(server_info.get('tools', []))}\n"
            
            for tool in server_info.get('tools', []):
                summary += f"      • {tool['name']}: {tool.get('description', 'No description')}\n"
        
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

class MCPToolExecutor:
    def __init__(self, server_manager):
        self.server_manager = server_manager
        self.intent_patterns = self._load_intent_patterns()
        self.csv_mappings = self._load_csv_mappings()
        self.column_patterns = self._load_column_patterns()
        
    def _load_intent_patterns(self) -> Dict[str, Dict]:
        return {
            'analyze_data': {
                'keywords': ['analizar', 'analiza', 'analyze', 'estadisticas', 'statistics', 'resumen', 'summary'],
                'server': 'csv_analysis',
                'tool': 'analyze_csv'
            },
            'create_visualization': {
                'keywords': ['histograma', 'grafico', 'visualiza', 'crea un', 'boxplot', 'scatter', 'visualizacion', 'grafica', 'plot', 'chart'],
                'server': 'csv_analysis',
                'tool': 'create_csv_visualization'
            },
            'detect_outliers': {
                'keywords': ['outliers', 'atipicos', 'anomalias', 'valores extremos', 'outlier', 'anomaly'],
                'server': 'csv_analysis',
                'tool': 'detect_outliers_in_csv'
            },
            'correlations': {
                'keywords': ['correlacion', 'correlaciones', 'correlation', 'relacion', 'relationship'],
                'server': 'csv_analysis',
                'tool': 'calculate_correlations_csv'
            },
            'filter_data': {
                'keywords': ['filtra', 'filter', 'filtrar', 'mostrar solo', 'show only', 'where', 'condicion'],
                'server': 'csv_analysis',
                'tool': 'filter_csv_data'
            },
            'clean_data': {
                'keywords': ['limpia', 'clean', 'limpiar', 'duplicados', 'duplicates', 'valores nulos', 'missing values', 'null'],
                'server': 'csv_analysis',
                'tool': 'clean_csv_data'
            },
            'group_data': {
                'keywords': ['agrupa', 'group', 'agrupar', 'group by', 'suma por', 'promedio por', 'aggregation'],
                'server': 'csv_analysis',
                'tool': 'group_csv_data'
            },
            'file_operation': {
                'keywords': ['lista', 'list', 'archivos', 'files', 'directorio', 'workspace', 'debug'],
                'server': 'filesystem',
                'tool': 'list_directory'
            }
        }
    
    def _load_csv_mappings(self) -> Dict[str, str]:
        mappings = {
            'estudiantes': 'estudiantes.csv',
            'ventas': 'ventas.csv', 
            'simple': 'simple.csv',
            'sales': 'sales.csv',
            'data': 'data.csv',
            'datos': 'datos.csv'
        }
        
        env_mappings = os.getenv('CSV_MAPPINGS')
        if env_mappings:
            try:
                additional_mappings = json.loads(env_mappings)
                mappings.update(additional_mappings)
            except:
                pass
                
        return mappings
    
    def _load_column_patterns(self) -> List[str]:
        return [
            r'columna\s+(\w+)',
            r'de\s+la?\s+(\w+)',
            r'variable\s+(\w+)',
            r'campo\s+(\w+)',
            r'column\s+(\w+)',
            r'para\s+(\w+)',
            r'sobre\s+(\w+)'
        ]
        
    def detect_tool_intent(self, user_input: str) -> dict:
        user_lower = user_input.lower()
        
        for intent_name, intent_config in self.intent_patterns.items():
            if any(keyword in user_lower for keyword in intent_config['keywords']):
                return {
                    'server': intent_config['server'],
                    'tool': intent_config['tool'],
                    'intent': intent_name
                }
        
        return {'intent': None}
    
    def extract_filter_conditions(self, user_input: str) -> List[Dict]:
        filters = []
        user_lower = user_input.lower()
        
        import re
        
        number_patterns = [
            r'mayor(?:es)?\s+(?:a|que)\s+(\d+(?:\.\d+)?)',
            r'greater\s+than\s+(\d+(?:\.\d+)?)',
            r'>\s*(\d+(?:\.\d+)?)',
            r'menor(?:es)?\s+(?:a|que)\s+(\d+(?:\.\d+)?)',
            r'less\s+than\s+(\d+(?:\.\d+)?)',
            r'<\s*(\d+(?:\.\d+)?)',
            r'igual(?:es)?\s+(?:a)?\s+(\d+(?:\.\d+)?)',
            r'equals?\s+(\d+(?:\.\d+)?)',
            r'=\s*(\d+(?:\.\d+)?)'
        ]
        
        for pattern in number_patterns:
            match = re.search(pattern, user_lower)
            if match:
                value = float(match.group(1))
                
                column = 'total'
                if 'precio' in user_lower or 'price' in user_lower:
                    column = 'precio'
                elif 'cantidad' in user_lower or 'quantity' in user_lower:
                    column = 'cantidad'
                elif 'descuento' in user_lower or 'discount' in user_lower:
                    column = 'descuento'
                
                if 'mayor' in pattern or 'greater' in pattern or '>' in pattern:
                    operator = 'greater_than'
                elif 'menor' in pattern or 'less' in pattern or '<' in pattern:
                    operator = 'less_than'
                else:
                    operator = 'equals'
                
                filters.append({
                    'column': column,
                    'operator': operator,
                    'value': value
                })
                break
        
        if 'region' in user_lower and ('norte' in user_lower or 'sur' in user_lower or 'este' in user_lower or 'oeste' in user_lower):
            region_match = re.search(r'region\s+(\w+)', user_lower)
            if region_match:
                filters.append({
                    'column': 'region',
                    'operator': 'equals',
                    'value': region_match.group(1).title()
                })
        
        if 'contiene' in user_lower or 'contains' in user_lower:
            contains_match = re.search(r'contiene\s+[\'"]?(\w+)[\'"]?', user_lower)
            if not contains_match:
                contains_match = re.search(r'contains\s+[\'"]?(\w+)[\'"]?', user_lower)
            
            if contains_match:
                column = 'producto'
                filters.append({
                    'column': column,
                    'operator': 'contains',
                    'value': contains_match.group(1)
                })
        
        return filters if filters else [{'column': 'total', 'operator': 'greater_than', 'value': 1000}]
    
    def extract_cleaning_operations(self, user_input: str) -> List[str]:
        operations = []
        user_lower = user_input.lower()
        
        if 'duplicados' in user_lower or 'duplicates' in user_lower:
            operations.append('drop_duplicates')
        
        if 'valores nulos' in user_lower or 'missing values' in user_lower or 'nulos' in user_lower:
            if 'elimina' in user_lower or 'drop' in user_lower:
                operations.append('drop_missing')
            elif 'rellena' in user_lower or 'fill' in user_lower:
                if 'media' in user_lower or 'mean' in user_lower or 'promedio' in user_lower:
                    operations.append('fill_missing_mean')
                elif 'moda' in user_lower or 'mode' in user_lower:
                    operations.append('fill_missing_mode')
                else:
                    operations.append('fill_missing_mean')  
        
        return operations if operations else ['drop_duplicates']
    
    def extract_grouping_info(self, user_input: str) -> tuple:
        user_lower = user_input.lower()
        
        group_by = []
        aggregations = {}
        
        if 'por region' in user_lower or 'by region' in user_lower:
            group_by.append('region')
        if 'por producto' in user_lower or 'by product' in user_lower:
            group_by.append('producto')
        if 'por vendedor' in user_lower or 'by seller' in user_lower:
            group_by.append('vendedor')
        if 'por mes' in user_lower or 'by month' in user_lower:
            group_by.append('fecha') 
        
        if 'suma' in user_lower or 'sum' in user_lower:
            if 'total' in user_lower or 'ventas' in user_lower:
                aggregations['total'] = 'sum'
            if 'cantidad' in user_lower:
                aggregations['cantidad'] = 'sum'
        
        if 'promedio' in user_lower or 'average' in user_lower or 'mean' in user_lower:
            if 'total' in user_lower or 'ventas' in user_lower:
                aggregations['total'] = 'mean'
            if 'precio' in user_lower:
                aggregations['precio'] = 'mean'
            if 'descuento' in user_lower:
                aggregations['descuento'] = 'mean'
        
        if 'conteo' in user_lower or 'count' in user_lower:
            aggregations['total'] = 'count'
        
        if not group_by:
            group_by = ['region']
        if not aggregations:
            aggregations = {'total': 'sum', 'cantidad': 'mean'}
        
        return group_by, aggregations
    
    def should_save_result(self, user_input: str) -> bool:
        user_lower = user_input.lower()
        save_keywords = ['guarda', 'save', 'guardar', 'exporta', 'export']
        return any(keyword in user_lower for keyword in save_keywords)
    
    async def execute_tool_for_intent(self, intent_data: dict, user_input: str) -> str:
        try:
            if intent_data['intent'] == 'analyze_data':
                csv_file = self.extract_csv_filename(user_input)
                if not csv_file:
                    return "Error: Could not identify CSV file to analyze. Please mention the filename (e.g., ventas.csv)"
                
                result = await self.server_manager.call_tool(
                    'csv_analysis', 
                    'analyze_csv',
                    {'file_path': csv_file}
                )
                return self.extract_text_from_result(result)
            
            elif intent_data['intent'] == 'create_visualization':
                csv_file = self.extract_csv_filename(user_input)
                if not csv_file:
                    return "Error: Could not identify CSV file. Please mention the filename (e.g., ventas.csv)"
                
                plot_type = self.extract_plot_type(user_input)
                column = self.extract_column_name(user_input)
                
                if not column:
                    return "Error: Could not identify column to visualize. Please specify the column"
                
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{csv_file.replace('.csv', '')}_{plot_type}_{column}_{timestamp}.png"
                
                result = await self.server_manager.call_tool(
                    'csv_analysis',
                    'create_csv_visualization', 
                    {
                        'file_path': csv_file,
                        'plot_type': plot_type,
                        'columns': [column],
                        'filename': filename
                    }
                )
                return self.extract_text_from_result(result)
            
            elif intent_data['intent'] in ['detect_outliers', 'correlations']:
                csv_file = self.extract_csv_filename(user_input)
                if not csv_file:
                    return "Error: Could not identify CSV file"
                
                result = await self.server_manager.call_tool(
                    'csv_analysis',
                    intent_data['tool'],
                    {'file_path': csv_file}
                )
                return self.extract_text_from_result(result)
            
            elif intent_data['intent'] == 'filter_data':
                csv_file = self.extract_csv_filename(user_input)
                if not csv_file:
                    return "Error: Could not identify CSV file"
                
                filters = self.extract_filter_conditions(user_input)
                save_filtered = self.should_save_result(user_input)
                
                result = await self.server_manager.call_tool(
                    'csv_analysis',
                    'filter_csv_data',
                    {
                        'file_path': csv_file,
                        'filters': filters,
                        'save_filtered': save_filtered
                    }
                )
                return self.extract_text_from_result(result)
            
            elif intent_data['intent'] == 'clean_data':
                csv_file = self.extract_csv_filename(user_input)
                if not csv_file:
                    return "Error: Could not identify CSV file"
                
                operations = self.extract_cleaning_operations(user_input)
                save_cleaned = self.should_save_result(user_input)
                
                result = await self.server_manager.call_tool(
                    'csv_analysis',
                    'clean_csv_data',
                    {
                        'file_path': csv_file,
                        'operations': operations,
                        'save_cleaned': save_cleaned
                    }
                )
                return self.extract_text_from_result(result)
            
            elif intent_data['intent'] == 'group_data':
                csv_file = self.extract_csv_filename(user_input)
                if not csv_file:
                    return "Error: Could not identify CSV file"
                
                group_by, aggregations = self.extract_grouping_info(user_input)
                save_grouped = self.should_save_result(user_input)
                
                result = await self.server_manager.call_tool(
                    'csv_analysis',
                    'group_csv_data',
                    {
                        'file_path': csv_file,
                        'group_by': group_by,
                        'aggregations': aggregations,
                        'save_grouped': save_grouped
                    }
                )
                return self.extract_text_from_result(result)
            
            elif intent_data['intent'] == 'file_operation':
                workspace_dir = self.server_manager.config['workspace_dir']
                result = await self.server_manager.call_tool(
                    'filesystem',
                    'list_directory',
                    {'path': workspace_dir}
                )
                return self.extract_text_from_result(result)
            
            return "Error: Could not execute the requested tool"
            
        except Exception as e:
            return f"Error executing MCP tool: {str(e)}"
    
    def extract_text_from_result(self, result: dict) -> str:
        try:
            if result.get('isError', False):
                error_msg = result.get('error', 'Unknown error')
                return f"Error in MCP tool: {error_msg}"
            
            content = result.get('content', [])
            if not content:
                return "MCP tool returned no content"
            
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
                return "Could not extract text from MCP result"
                
        except Exception as e:
            return f"Error processing MCP result: {str(e)}"
    
    def extract_csv_filename(self, text: str) -> str:
        csv_pattern = r'(\w+\.csv)'
        matches = re.findall(csv_pattern, text.lower())
        if matches:
            return matches[0]
        
        words = text.split()
        for word in words:
            clean_word = word.strip('.,!?()[]{}";:')
            if clean_word.endswith('.csv'):
                return clean_word
        
        text_lower = text.lower()
        for keyword, filename in self.csv_mappings.items():
            if keyword in text_lower:
                return filename
        
        return None
    
    def extract_plot_type(self, text: str) -> str:
        text_lower = text.lower()
        
        plot_mappings = {
            'histograma': 'histogram',
            'boxplot': 'boxplot',
            'box plot': 'boxplot',
            'scatter': 'scatter',
            'dispersion': 'scatter',
            'correlacion': 'correlation_heatmap',
            'correlation': 'correlation_heatmap',
            'bar': 'bar',
            'barras': 'bar'
        }
        
        for keyword, plot_type in plot_mappings.items():
            if keyword in text_lower:
                return plot_type
                
        return 'histogram'
    
    def extract_column_name(self, text: str) -> str:
        for pattern in self.column_patterns:
            match = re.search(pattern, text.lower())
            if match:
                return match.group(1)
        
        known_columns = ['edad', 'calificacion', 'asistencia', 'semestre', 'nombre', 'id', 'age', 'score', 'attendance', 'precio', 'cantidad', 'total', 'descuento', 'region', 'producto', 'vendedor']
        for col in known_columns:
            if col in text.lower():
                return col
        
        return None

class MCPChatbot:
    def __init__(self, api_key: Optional[str] = None):
        self.llm_client = AnthropicLLMClient(api_key)
        self.context = ConversationContext()
        self.mcp_logger = MCPLogger()
        self.server_manager = MCPServerManager(self.mcp_logger)
        
        workspace_dir = self.server_manager.config['workspace_dir']
        system_message = f"""You are an intelligent assistant specialized in data analysis using MCP tools.

Your main function is to interpret the results you receive from MCP tools and present them clearly and usefully.

AVAILABLE FILES in {workspace_dir}/:
- estudiantes.csv (academic student data)
- ventas.csv (commercial data)  
- simple.csv (simple test data)

IMPORTANT: 
- When you receive results from MCP tools, interpret and present them clearly
- If you don't receive tool results, explain that you need to execute the corresponding tools
- Maintain a professional and pedagogical tone
- Highlight the most important insights from analyses

Workspace: {workspace_dir}"""
        
        self.context.add_message("system", system_message)
    
    async def initialize_servers(self):
        workspace_dir = self.server_manager.config['workspace_dir']
        os.makedirs(workspace_dir, exist_ok=True)
        
        await self.server_manager.add_filesystem_server("./mcp_workspace")
        await self.server_manager.add_git_server("./mcp_workspace")
        await self.server_manager.add_csv_analysis_server("/Users/josepereira/Documents/GitHub/mcp_server/csv_mcp_server.py")
        
        if self.server_manager.external_servers:
            successful, failed = await self.server_manager.connect_external_servers()
    
    def process_special_command(self, user_input: str) -> bool:
        command = user_input.strip().lower()
        return command in ["/quit", "/tools", "/external", "/registered", "/log", "/context", "/clear", "/help"]
    
    async def cleanup(self):
        try:
            cleanup_task = asyncio.create_task(self.server_manager.cleanup())
            await asyncio.wait_for(cleanup_task, timeout=5.0)
        except asyncio.TimeoutError:
            pass
        except Exception as e:
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
        
        welcome_text = """Welcome to the [bold bright_blue]MCP Chatbot[/bold bright_blue]!
        """
        
        welcome_panel = Panel(
            Markdown(welcome_text),
            border_style=self.colors['primary'],
            padding=(1, 2),
            title="[bold bright_blue]Chatbot[/bold bright_blue]"
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
            ("Chat", "Chat with assistant", "/chat"),
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
    
    def show_server_status(self, servers: dict):
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
        
        self.console.print(status_table)
        
    
    def show_tools_grid(self, all_tools: dict):
        panels = []
        
        for server_name, tools in all_tools.items():
            tool_list = []
            for tool in tools:
                tool_list.append(f"• {tool['name']}")
            
            if server_name == "csv_analysis":
                border_color = self.colors['success']
                title_prefix = "[Real Execution] "
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
            "[bold]Chat Mode[/bold]\n\n"
            "[cyan]Check log with /log to confirm tool execution[/cyan]\n\n"
            "Special commands:\n"
            f"[{self.colors['muted']}]• /tools - View available MCP tools[/]\n"
            f"[{self.colors['muted']}]• /log - View MCP call history[/]\n"
            f"[{self.colors['muted']}]• /clear - Clear context[/]\n"
            f"[{self.colors['muted']}]• /menu - Return to main menu[/]",
            border_style=self.colors['accent'],
            title="Chat Interface"
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
            title = "Claude [Real MCP]"
        
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
        log_table = Table(title=f"Last {limit} MCP Interactions - Execution Verification")
        log_table.add_column("Time", style=self.colors['muted'])
        log_table.add_column("Server", style=self.colors['primary'])
        log_table.add_column("Tool", style=self.colors['secondary'])
        log_table.add_column("Status", justify="center")
        
        recent_interactions = interactions[-limit:] if interactions else []
        
        if not recent_interactions:
            no_interactions_panel = Panel(
                "[yellow]NO MCP INTERACTIONS REGISTERED[/yellow]\n\n"
                "[red]This could indicate that:[/red]\n"
                "• Claude is not using MCP tools\n"
                "• Queries don't require specific tools\n"
                "• There's a connection problem with servers\n\n"
                "[cyan]Try making a specific CSV query[/cyan]",
                border_style="yellow",
                title="Warning"
            )
            self.console.print(no_interactions_panel)
            return
        
        csv_interactions_count = 0
        for interaction in recent_interactions:
            timestamp = interaction.timestamp.split('T')[1].split('.')[0]
            
            if interaction.server_name == "csv_analysis":
                csv_interactions_count += 1
            
            if interaction.status == "success":
                status_display = f"[{self.colors['success']}]✓[/]"
            else:
                status_display = f"[{self.colors['error']}]✗[/]"
            
            if interaction.server_name == "csv_analysis":
                server_display = f"[bold green]{interaction.server_name}[/bold green]"
                tool_display = f"[bold green]{interaction.request_type}[/bold green]"
            else:
                server_display = interaction.server_name
                tool_display = interaction.request_type
            
            log_table.add_row(
                timestamp,
                server_display,
                tool_display,
                status_display
            )
        
        self.console.print(log_table)
        
        summary_panel = Panel(
            f"[green]CSV interactions detected: {csv_interactions_count}[/green]\n"
            f"[cyan]Total interactions: {len(recent_interactions)}[/cyan]\n"
            f"[yellow]{'MCP SERVER BEING USED CORRECTLY' if csv_interactions_count > 0 else 'NO CSV SERVER CALLS DETECTED'}[/yellow]",
            title="MCP Usage Summary",
            border_style="green" if csv_interactions_count > 0 else "yellow"
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
    
    def show_mcp_verification_reminder(self):
        reminder_panel = Panel(
            "[yellow]REMINDER:[/yellow]\n"
            "Use [bold cyan]/log[/bold cyan] to verify MCP tools are being used\n",
            border_style="cyan",
            title="MCP Verification"
        )
        self.console.print(reminder_panel)

class EnhancedMCPChatbot(MCPChatbot):
    def __init__(self, api_key=None):
        super().__init__(api_key)
        self.ui = EnhancedTerminalUI()
        self.current_mode = "menu"
        self.tool_executor = None
    
    async def test_csv_server_connection(self):
        try:
            if not await self.server_manager.validate_server_connection("csv_analysis"):
                return False
            
            result = await self.server_manager.call_tool(
                "csv_analysis", 
                "debug_workspace", 
                {}
            )
            
            if result.get("isError", False):
                return False
            
            return True
            
        except Exception as e:
            return False
    
    async def enhanced_chat_loop(self):
        self.ui.show_welcome_screen()
        
        progress, task = self.ui.show_progress("Initializing MCP servers...")
        with progress:
            await self.initialize_servers()
            self.tool_executor = MCPToolExecutor(self.server_manager)
            
            csv_working = await self.test_csv_server_connection()
            if not csv_working:
                self.ui.show_error("CSV server is not working correctly")
        
        self.ui.show_success("All MCP servers initialized")
        
        while True:
            try:
                if self.current_mode == "menu":
                    self.ui.show_main_menu()
                    self.ui.show_server_status(self.server_manager.servers)
                    
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
                    
                    intent = self.tool_executor.detect_tool_intent(user_input)
                    
                    mcp_result = None
                    if intent['intent']:
                        progress, task = self.ui.show_progress("Executing real MCP tools...")
                        try:
                            with progress:
                                mcp_result = await self.execute_mcp_tool_safely(intent, user_input)
                        except Exception as e:
                            mcp_result = f"Error executing MCP tool: {str(e)}"
                    
                    if mcp_result:
                        enhanced_input = f"""User asked: {user_input}

Result from MCP tool (REAL):
{mcp_result}

Interpret and present this result clearly and usefully for the user. Highlight the most important insights and present the information in an organized way."""
                        
                        self.context.add_message("user", enhanced_input)
                    else:
                        self.context.add_message("user", user_input)
                    
                    progress, task = self.ui.show_progress("Claude interpreting results...")
                    try:
                        with progress:
                            system_message, messages = self.context.get_messages_for_api()
                            response = self.llm_client.send_message(system_message, messages)
                    except Exception as e:
                        response = f"Error communicating with Claude: {str(e)}"
                    
                    self.context.add_message("assistant", response)
                    self.ui.display_message("assistant", response)
                    
                    if any(keyword in user_input.lower() for keyword in ["csv", "archivo", "analizar", "estadisticas", "correlacion", "outliers", "estudiantes", "ventas", "simple", "histograma", "grafico"]):
                        self.ui.show_mcp_verification_reminder()
                    
            except KeyboardInterrupt:
                if self.ui.get_confirmation("\nDo you want to exit the program?"):
                    break
                continue
            except Exception as e:
                self.ui.show_error(f"Unexpected error: {str(e)}")
    
    async def execute_mcp_tool_safely(self, intent, user_input):
        try:
            result = await asyncio.wait_for(
                self.tool_executor.execute_tool_for_intent(intent, user_input),
                timeout=30.0
            )
            return result
        except asyncio.TimeoutError:
            return "Timeout: MCP tool took too long to respond"
        except Exception as e:
            return f"Error in MCP tool: {str(e)}"
    
    def show_tools_interface(self):
        self.ui.clear_screen()
        all_tools = self.server_manager.get_all_tools()
        self.ui.show_tools_grid(all_tools)
        input("\nPress Enter to continue...")
    
    def show_servers_interface(self):
        self.ui.clear_screen()
        self.ui.show_server_status(self.server_manager.servers)
        
        if self.server_manager.external_servers:
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
        console.print(f"[red]Error initializing chatbot: {str(e)}[/red]")

if __name__ == "__main__":
    asyncio.run(enhanced_main())