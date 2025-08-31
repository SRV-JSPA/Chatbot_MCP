import json
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
import anthropic
import os
from dataclasses import dataclass


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
        self.model = "claude-opus-4-1-20250805"  
    
    def send_message(self, messages: List[Dict[str, str]], max_tokens: int = 1024) -> str:
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
        
        system_message = """Eres un asistente inteligente que puede usar herramientas MCP (Model Context Protocol) para realizar tareas complejas. 
        
Tus capacidades incluyen:
- Responder preguntas generales usando tu conocimiento base
- Mantener contexto en conversaciones largas
- Usar servidores MCP para tareas específicas como análisis de datos, manipulación de archivos, etc.

Siempre sé útil, preciso y mantén un tono amigable y profesional."""
        
        self.context.add_message("system", system_message)
        
        print("Chatbot MCP inicializado correctamente!")
        print("\nComandos especiales:")
        print("   /log - Mostrar log de interacciones MCP")
        print("   /context - Mostrar resumen de la conversación")
        print("   /clear - Limpiar contexto de conversación")
        print("   /quit - Salir del chatbot")
    
    def process_special_command(self, user_input: str) -> bool:
        command = user_input.strip().lower()
        
        if command == "/log":
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
        
        elif command == "/quit":
            print("¡Hasta luego! Gracias por usar el chatbot MCP.")
            return True
        
        return False
    
    def simulate_mcp_interaction(self, server_name: str, request_type: str, 
                                request_data: Dict[Any, Any]) -> Dict[Any, Any]:    
        response_data = {
            "status": "success",
            "message": f"Simulación de respuesta del servidor {server_name}",
            "timestamp": datetime.now().isoformat(),
            "data": {"result": "Operación completada exitosamente"}
        }
        
        self.mcp_logger.log_interaction(
            server_name=server_name,
            request_type=request_type,
            request_data=request_data,
            response_data=response_data
        )
        
        return response_data
    
    def chat_loop(self):
        print("\n¡Chatbot listo! Escribe tu mensaje o usa comandos especiales.\n")
        
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
                
                if any(keyword in user_input.lower() for keyword in ["csv", "análisis", "datos", "archivo"]):
                    self.simulate_mcp_interaction(
                        server_name="csv_analysis_server",
                        request_type="analyze_data",
                        request_data={"query": user_input, "file": "example.csv"}
                    )
                
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


def main():
    print("="*80)
    print("CHATBOT MCP")
    print("="*80)
    
    try:
        if not os.getenv("ANTHROPIC_API_KEY"):
            print("\nNo se encontró la API key de Anthropic.")
            return
        
        
        chatbot = MCPChatbot()
        chatbot.chat_loop()
        
    except Exception as e:
        print(f"\nError al inicializar el chatbot: {str(e)}")

if __name__ == "__main__":
    main()