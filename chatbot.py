import json
import logging
import asyncio
import os
import sys
from datetime import datetime
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from contextlib import AsyncExitStack
from dotenv import load_dotenv

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
        self.logger.debug(f"Request: {json.dumps(request_data, indent=2)}")
        self.logger.debug(f"Response: {json.dumps(response_data, indent=2)}")
    
    def show_interactions_log(self, limit: Optional[int] = None):
        interactions_to_show = self.interactions[-limit:] if limit else self.interactions
        
        print("\n" + "="*80)
        print("LOG DE INTERACCIONES MCP")
        print("="*80)
        
        if not interactions_to_show:
            print("No hay interacciones registradas.")
            return
        
        for i, interaction in enumerate(interactions_to_show, 1):
            print(f"\n[{i}] {interaction.timestamp}")
            print(f"    Servidor: {interaction.server_name}")
            print(f"    Tipo: {interaction.request_type}")
            print(f"    Estado: {interaction.status}")
            print(f"    Request: {json.dumps(interaction.request_data, indent=6)}")
            print(f"    Response: {json.dumps(interaction.response_data, indent=6)}")
        
        print("="*80)


class MCPServerManager:
    def __init__(self, logger: MCPLogger):
        self.logger = logger
        self.servers: Dict[str, Dict[str, Any]] = {}
        self.exit_stack = AsyncExitStack()
        self.external_servers: List[ExternalMCPServer] = []
    
    
    async def add_filesystem_server(self, allowed_path: str = "./"):
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
            
            await session.initialize()
            
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
            print(f"Error conectando servidor Filesystem: {str(e)}")
    
    async def add_git_server(self, repository_path: str = "./"):
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
            
            await session.initialize()
            
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
            
            print(f"Servidor Git conectado exitosamente")
            print(f"   Directorio: {repository_path}")
            print(f"   Herramientas disponibles: {len(tools)}")
            for tool in tools:
                print(f"      • {tool['name']}: {tool.get('description', 'Sin descripción')}")
            
        except Exception as e:
            self.logger.log_interaction(
                "git", 
                "server_connection",
                {"repository_path": repository_path},
                {"error": str(e)},
                "error"
            )
            print(f"Error conectando servidor Git: {str(e)}")
            print("Consejo: Verificar que 'uvx mcp-server-git' funcione desde terminal")
    
    async def add_csv_analysis_server(self, server_path: str = "/Users/josepereira/Documents/GitHub/mcp_server/csv_mcp_server.py"):
        try:
            server_params = StdioServerParameters(
                command="python",
                args=[server_path],
                env=None
            )
            
            stdio_transport = await self.exit_stack.enter_async_context(
                stdio_client(server_params)
            )
            stdio, write = stdio_transport
            session = await self.exit_stack.enter_async_context(
                ClientSession(stdio, write)
            )
            
            await session.initialize()
            
            tools_response = await session.list_tools()
            tools = [{"name": tool.name, "description": tool.description} for tool in tools_response.tools]
            
            self.servers["csv_analysis"] = {
                "session": session,
                "tools": tools,
                "status": "connected",
                "server_path": server_path,
                "type": "own"
            }
            
            self.logger.log_interaction(
                "csv_analysis", 
                "server_connection",
                {"server_path": server_path},
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
            print(f"Error conectando servidor CSV Analysis: {str(e)}")
    
    
    
    def register_external_server(self, external_server: ExternalMCPServer):
        self.external_servers.append(external_server)
        print(f"Servidor externo '{external_server.name}' registrado")
        if external_server.author:
            print(f"   Autor: {external_server.author}")
        if external_server.repository_url:
            print(f"   Repositorio: {external_server.repository_url}")
    
    async def add_external_server(self, server_name: str) -> bool:
        external_server = next(
            (server for server in self.external_servers if server.name == server_name), 
            None
        )
        
        if not external_server:
            print(f"Error: Servidor externo '{server_name}' no encontrado")
            return False
        
        try:
            
            original_cwd = os.getcwd()
            if external_server.working_directory:
                if os.path.exists(external_server.working_directory):
                    os.chdir(external_server.working_directory)
                else:
                    print(f"Advertencia: Directorio {external_server.working_directory} no existe")
            
            
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
            
            await session.initialize()
            
            
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
            
            print(f"Servidor externo '{server_name}' conectado exitosamente")
            if external_server.author:
                print(f"   Autor: {external_server.author}")
            print(f"   Herramientas disponibles: {len(tools)}")
            for tool in tools:
                print(f"      • {tool['name']}: {tool.get('description', 'Sin descripción')}")
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
            print(f"Error conectando servidor externo '{server_name}': {str(e)}")
            return False
    
    async def connect_external_servers(self, server_names: List[str] = None):
        if server_names is None:
            server_names = [server.name for server in self.external_servers]
        
        if not server_names:
            print("No hay servidores externos registrados para conectar")
            return [], []
        
        successful_connections = []
        failed_connections = []
        
        for server_name in server_names:
            if await self.add_external_server(server_name):
                successful_connections.append(server_name)
            else:
                failed_connections.append(server_name)
        
        print(f"\nResumen de conexiones externas:")
        print(f"   Exitosas: {len(successful_connections)} - {successful_connections}")
        if failed_connections:
            print(f"   Fallidas: {len(failed_connections)} - {failed_connections}")
        
        return successful_connections, failed_connections
    
    
    
    async def call_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if server_name not in self.servers:
            raise ValueError(f"Servidor {server_name} no está conectado")
        
        server = self.servers[server_name]
        if server["status"] != "connected":
            raise ValueError(f"Servidor {server_name} no está activo")
        
        try:
            session = server["session"]
            result = await session.call_tool(tool_name, arguments)
            
            response_data = {
                "content": [{"type": content.type, "text": content.text if hasattr(content, 'text') else str(content)} 
                           for content in result.content],
                "isError": result.isError if hasattr(result, 'isError') else False,
                "server_type": server.get("type", "unknown"),
                "author": server.get("author", "N/A")
            }
            
            self.logger.log_interaction(
                server_name,
                f"tool_call:{tool_name}",
                arguments,
                response_data
            )
            
            return response_data
        
        except Exception as e:
            error_response = {
                "error": str(e), 
                "isError": True,
                "server_type": server.get("type", "unknown")
            }
            self.logger.log_interaction(
                server_name,
                f"tool_call:{tool_name}",
                arguments,
                error_response,
                "error"
            )
            return error_response
    
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
            return "No hay servidores externos conectados."
        
        summary = f"Servidores externos conectados ({len(external_servers)}):\n"
        summary += "=" * 50 + "\n"
        
        for server_name, server_info in external_servers.items():
            summary += f"\n{server_name}\n"
            summary += f"   Autor: {server_info.get('author', 'N/A')}\n"
            summary += f"   Descripción: {server_info.get('description', 'N/A')}\n"
            summary += f"   Repositorio: {server_info.get('repository_url', 'N/A')}\n"
            summary += f"   Herramientas: {len(server_info.get('tools', []))}\n"
            
            for tool in server_info.get('tools', []):
                summary += f"      • {tool['name']}: {tool.get('description', 'Sin descripción')}\n"
        
        return summary
    
    def get_registered_servers_info(self) -> str:
        if not self.external_servers:
            return "No hay servidores externos registrados."
        
        summary = f"Servidores externos registrados ({len(self.external_servers)}):\n"
        summary += "=" * 50 + "\n"
        
        for server in self.external_servers:
            status = "Conectado" if server.name in self.servers else "No conectado"
            summary += f"\n{server.name} - {status}\n"
            summary += f"   Autor: {server.author or 'N/A'}\n"
            summary += f"   Descripción: {server.description}\n"
            summary += f"   Comando: {server.command} {' '.join(server.args)}\n"
            if server.repository_url:
                summary += f"   Repositorio: {server.repository_url}\n"
        
        return summary
    
    async def cleanup(self):
        await self.exit_stack.aclose()


class ConversationContext:    
    def __init__(self, max_messages: int = 50):
        self.messages: List[Dict[str, str]] = []
        self.max_messages = max_messages
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
Resumen de la conversación:
- Inicio: {self.conversation_start.strftime("%Y-%m-%d %H:%M:%S")}
- Duración: {duration}
- Total de mensajes: {total_messages}
- Mensajes del usuario: {user_messages}
- Mensajes del asistente: {assistant_messages}
        """.strip()


class AnthropicLLMClient:    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("Se requiere una API key de Anthropic.")
        
        self.client = anthropic.Anthropic(api_key=self.api_key)
        
        self.model = "claude-sonnet-4-20250514"
    
    def send_message(self, system_message: str, messages: List[Dict[str, str]], max_tokens: int = 2048) -> str:
        try:
            
            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system_message,  
                messages=messages
            )
            return response.content[0].text
        except Exception as e:
            return f"Error al comunicarse con Claude: {str(e)}"


class MCPChatbot:
    
    def __init__(self, api_key: Optional[str] = None):
        self.llm_client = AnthropicLLMClient(api_key)
        self.context = ConversationContext()
        self.mcp_logger = MCPLogger()
        self.server_manager = MCPServerManager(self.mcp_logger)
        
        system_message = """Eres un asistente inteligente que puede usar herramientas MCP (Model Context Protocol) para realizar tareas complejas. 

Tienes acceso a las siguientes capacidades a través de servidores MCP:

SERVIDORES OFICIALES:
- Filesystem: operaciones de archivos seguras (leer, escribir, listar directorios)
- Git: manipulación de repositorios Git (commits, branches, status, etc.)

SERVIDOR PROPIO:
- CSV Analysis: análisis avanzado de datos CSV (estadísticas, visualizaciones, limpieza de datos)

SERVIDORES EXTERNOS:
Los servidores externos desarrollados por otros estudiantes se conectan dinámicamente y 
proporcionan funcionalidades adicionales específicas según cada implementación.

Cuando el usuario solicite operaciones que requieran estas herramientas, usa las funciones MCP apropiadas.
Puedes combinar múltiples servidores para resolver problemas complejos.
Siempre sé útil, preciso y mantén un tono amigable y profesional."""
        
        self.context.add_message("system", system_message)
    
    async def initialize_servers(self):
        
        os.makedirs("./mcp_workspace", exist_ok=True)
        
        
        print("   • Conectando servidor Filesystem...")
        await self.server_manager.add_filesystem_server("./mcp_workspace")
        
        print("   • Conectando servidor Git...")
        await self.server_manager.add_git_server("./mcp_workspace")
        
        
        print("   • Conectando servidor CSV Analysis...")
        await self.server_manager.add_csv_analysis_server("/Users/josepereira/Documents/GitHub/mcp_server/csv_mcp_server.py")
        
        
        if self.server_manager.external_servers:
            print("   • Conectando servidores externos...")
            successful, failed = await self.server_manager.connect_external_servers()
        else:
            print("   • No hay servidores externos registrados")
    
    def add_external_server_config(self, name: str, description: str, command: str, 
                                  args: List[str], author: str = None, 
                                  repository_url: str = None, working_directory: str = None,
                                  env_vars: Dict[str, str] = None):
        external_server = ExternalMCPServer(
            name=name,
            description=description,
            command=command,
            args=args,
            working_directory=working_directory,
            env_vars=env_vars,
            repository_url=repository_url,
            author=author
        )
        self.server_manager.register_external_server(external_server)
    
    def process_special_command(self, user_input: str) -> bool:
        command = user_input.strip().lower()
        
        if command == "/tools":
            print("\n" + "="*80)
            print("HERRAMIENTAS MCP DISPONIBLES")
            print("="*80)
            all_tools = self.server_manager.get_all_tools()
            for server_name, tools in all_tools.items():
                server_info = self.server_manager.servers.get(server_name, {})
                server_type = server_info.get("type", "unknown")
                author = server_info.get("author", "N/A")
                
                print(f"\n🔧 Servidor: {server_name} ({server_type.upper()})")
                if server_type == "external":
                    print(f"   Autor: {author}")
                
                for tool in tools:
                    print(f"   • {tool['name']}: {tool.get('description', 'Sin descripción')}")
            print("="*80)
            return True
        
        elif command == "/external":
            print(self.server_manager.get_external_servers_summary())
            return True
        
        elif command == "/registered":
            print(self.server_manager.get_registered_servers_info())
            return True
        
        elif command == "/log":
            self.mcp_logger.show_interactions_log(limit=10)
            return True
        
        elif command == "/context":
            print("\n" + "="*60)
            print(self.context.get_conversation_summary())
            print("="*60)
            return True
        
        elif command == "/clear":
            
            system_msg = self.context.system_message
            self.context.messages = []
            self.context.system_message = system_msg
            print("Contexto de conversación limpiado.")
            return True
        
        elif command == "/help":
            self.show_help()
            return True
        
        elif command == "/quit":
            print("¡Hasta luego! Gracias por usar el chatbot MCP.")
            return True
        
        return False
    
    def show_help(self):
        print("\n" + "="*60)
        print("COMANDOS ESPECIALES DISPONIBLES")
        print("="*60)
        print("   /tools       - Muestra todas las herramientas MCP disponibles")
        print("   /external    - Muestra servidores externos conectados")
        print("   /registered  - Muestra servidores externos registrados")
        print("   /log         - Muestra las últimas 10 interacciones MCP")
        print("   /context     - Muestra resumen del contexto de conversación")
        print("   /clear       - Limpia el contexto de conversación")
        print("   /help        - Muestra esta ayuda")
        print("   /quit        - Termina el programa")
        print("="*60)
    
    async def process_mcp_request(self, user_input: str) -> Dict[str, Any]:

        lower_input = user_input.lower()
        
        if any(keyword in lower_input for keyword in ["analizar", "análisis", "procesar", "evaluar"]):
            return {"suggestion": "analysis_request", "detected": True}
        
        
        if any(keyword in lower_input for keyword in ["archivo", "file", "documento", "csv", "txt"]):
            return {"suggestion": "file_operation", "detected": True}
        
        
        if any(keyword in lower_input for keyword in ["git", "commit", "repositorio", "branch"]):
            return {"suggestion": "git_operation", "detected": True}
        
        return {"suggestion": None, "detected": False}
    
    async def chat_loop(self):
        print("\n¡Chatbot MCP listo!")
        
        while True:
            try:
                user_input = input("Usuario: ").strip()
                
                if not user_input:
                    continue
                
                if user_input.startswith("/"):
                    if self.process_special_command(user_input):
                        if user_input.lower() == "/quit":
                            break
                        continue
                
                self.context.add_message("user", user_input)
                
                
                mcp_suggestion = await self.process_mcp_request(user_input)
                
                print("Pensando...", end="", flush=True)
                system_message, messages = self.context.get_messages_for_api()
                response = self.llm_client.send_message(system_message, messages)
                print("\r" + " " * 15 + "\r", end="")
                
                self.context.add_message("assistant", response)
                
                print(f"Claude: {response}\n")
                
            except KeyboardInterrupt:
                print("\n\n¡Hasta luego! Gracias por usar el chatbot MCP.")
                break
            except Exception as e:
                print(f"\nError inesperado: {str(e)}")
    
    async def cleanup(self):
        await self.server_manager.cleanup()


async def main():
    print("="*80)
    print("CHATBOT MCP")
    print("="*80)
    
    try:
        if not os.getenv("ANTHROPIC_API_KEY"):
            print("\nNo se encontró la API key de Anthropic.")
            return
        
        chatbot = MCPChatbot()
    
        await chatbot.initialize_servers()
        
        
        await chatbot.chat_loop()
        
        
        await chatbot.cleanup()
        
    except Exception as e:
        print(f"\nError al inicializar el chatbot: {str(e)}")

if __name__ == "__main__":
    asyncio.run(main())