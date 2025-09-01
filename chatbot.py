import json
import logging
import asyncio
import os
import sys
from datetime import datetime
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from contextlib import AsyncExitStack

import anthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@dataclass
class MCPInteraction:
    timestamp: str
    server_name: str
    request_type: str
    request_data: Dict[Any, Any]
    response_data: Dict[Any, Any]
    status: str


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
                "allowed_path": allowed_path
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
                command="npx",
                args=["-y", "@modelcontextprotocol/server-git", "--repository", repository_path],
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
            
            self.servers["git"] = {
                "session": session,
                "tools": tools,
                "status": "connected",
                "repository_path": repository_path
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
            print(f"Error conectando servidor Git: {str(e)}")
    
    async def add_csv_analysis_server(self, server_path: str = "./csv_analysis_server.py"):
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
                "server_path": server_path
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
                "isError": result.isError if hasattr(result, 'isError') else False
            }
            
            self.logger.log_interaction(
                server_name,
                f"tool_call:{tool_name}",
                arguments,
                response_data
            )
            
            return response_data
        
        except Exception as e:
            error_response = {"error": str(e), "isError": True}
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
                all_tools[server_name] = server_info["tools"]
        return all_tools
    
    async def cleanup(self):
        await self.exit_stack.aclose()


class ConversationContext:    
    def __init__(self, max_messages: int = 50):
        self.messages: List[Dict[str, str]] = []
        self.max_messages = max_messages
        self.conversation_start = datetime.now()
    
    def add_message(self, role: str, content: str):
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        }
        
        self.messages.append(message)
        
        if len(self.messages) > self.max_messages:
            system_messages = [msg for msg in self.messages if msg["role"] == "system"]
            recent_messages = self.messages[-(self.max_messages-len(system_messages)):]
            self.messages = system_messages + recent_messages
    
    def get_messages_for_api(self) -> List[Dict[str, str]]:
        return [{"role": msg["role"], "content": msg["content"]} for msg in self.messages]
    
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
        self.model = "claude-3-5-sonnet-20241022"
    
    def send_message(self, messages: List[Dict[str, str]], max_tokens: int = 2048) -> str:
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
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

Tienes acceso a las siguientes capacidades através de servidores MCP:
- Filesystem: operaciones de archivos seguras (leer, escribir, listar directorios)
- Git: manipulación de repositorios Git (commits, branches, status, etc.)
- CSV Analysis: análisis avanzado de datos CSV (estadísticas, visualizaciones, limpieza de datos)

Cuando el usuario solicite operaciones que requieran estas herramientas, usa las funciones MCP apropiadas.
Siempre sé útil, preciso y mantén un tono amigable y profesional."""
        
        self.context.add_message("system", system_message)
    
    async def initialize_servers(self):
        
        os.makedirs("./mcp_workspace", exist_ok=True)
        
        
        await self.server_manager.add_filesystem_server("./mcp_workspace")
        await self.server_manager.add_git_server("./mcp_workspace")
        
        
        await self.server_manager.add_csv_analysis_server()
            
    def process_special_command(self, user_input: str) -> bool:
        command = user_input.strip().lower()
        
        if command == "/tools":
            print("\n" + "="*60)
            print("HERRAMIENTAS MCP DISPONIBLES")
            print("="*60)
            all_tools = self.server_manager.get_all_tools()
            for server_name, tools in all_tools.items():
                print(f"\nServidor: {server_name}")
                for tool in tools:
                    print(f"   • {tool['name']}: {tool.get('description', 'Sin descripción')}")
            print("="*60)
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
            system_messages = [msg for msg in self.context.messages if msg["role"] == "system"]
            self.context.messages = system_messages
            print("Contexto de conversación limpiado.")
            return True
        
        # elif command == "/demo":
        #     asyncio.create_task(self.run_demo())
        #     return True
        
        elif command == "/quit":
            print("¡Hasta luego! Gracias por usar el chatbot MCP.")
            return True
        
        return False
    
    async def chat_loop(self):
        print("\n¡Chatbot MCP listo!.\n")
        
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
                
                
                mcp_response = await self.process_mcp_request(user_input)
                
                print("Pensando...", end="", flush=True)
                messages = self.context.get_messages_for_api()
                response = self.llm_client.send_message(messages)
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