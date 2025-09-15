from flask import Flask, request, jsonify
from flask_cors import CORS
import json
import asyncio
import psutil
import os
import platform
import socket
import time
from datetime import datetime
import traceback
import uuid
from typing import Dict, Any, List

app = Flask(__name__)
CORS(app)

@app.route("/")
def index():
    return jsonify({
        "service": "Remote MCP System Monitor Server",
        "status": "ok",
        "version": "1.0.0",
        "timestamp": datetime.now().isoformat(),
        "endpoints": ["/health", "/mcp/tools/list", "/mcp/tools/call"]
    })

class SystemMonitor:
    @staticmethod
    def get_cpu_info() -> Dict[str, Any]:
        try:
            cpu_info = {
                "usage_percent": psutil.cpu_percent(interval=1),
                "core_count": psutil.cpu_count(),
                "logical_cores": psutil.cpu_count(logical=True),
                "frequency": psutil.cpu_freq()._asdict() if psutil.cpu_freq() else None,
                "per_core_usage": psutil.cpu_percent(percpu=True, interval=0.5),
                "load_average": os.getloadavg() if hasattr(os, 'getloadavg') else None
            }
            return cpu_info
        except Exception as e:
            raise Exception(f"Error getting CPU info: {str(e)}")
    
    @staticmethod
    def get_memory_info() -> Dict[str, Any]:
        try:
            memory = psutil.virtual_memory()
            swap = psutil.swap_memory()
            
            memory_info = {
                "total_gb": round(memory.total / (1024**3), 2),
                "available_gb": round(memory.available / (1024**3), 2),
                "used_gb": round(memory.used / (1024**3), 2),
                "free_gb": round(memory.free / (1024**3), 2),
                "usage_percent": memory.percent,
                "swap_total_gb": round(swap.total / (1024**3), 2) if swap.total > 0 else 0,
                "swap_used_gb": round(swap.used / (1024**3), 2) if swap.used > 0 else 0,
                "swap_percent": swap.percent if swap.total > 0 else 0
            }
            return memory_info
        except Exception as e:
            raise Exception(f"Error getting memory info: {str(e)}")
    
    @staticmethod
    def get_disk_info() -> Dict[str, Any]:
        try:
            disk_info = {
                "partitions": [],
                "total_disk_io": psutil.disk_io_counters()._asdict() if psutil.disk_io_counters() else {}
            }
            
            partitions = psutil.disk_partitions()
            for partition in partitions:
                try:
                    usage = psutil.disk_usage(partition.mountpoint)
                    partition_info = {
                        "device": partition.device,
                        "mountpoint": partition.mountpoint,
                        "filesystem": partition.fstype,
                        "total_gb": round(usage.total / (1024**3), 2),
                        "used_gb": round(usage.used / (1024**3), 2),
                        "free_gb": round(usage.free / (1024**3), 2),
                        "usage_percent": round((usage.used / usage.total) * 100, 1)
                    }
                    disk_info["partitions"].append(partition_info)
                except PermissionError:
                    continue
            
            return disk_info
        except Exception as e:
            raise Exception(f"Error getting disk info: {str(e)}")
    
    @staticmethod
    def get_network_info() -> Dict[str, Any]:
        try:
            network_info = {
                "interfaces": {},
                "io_counters": psutil.net_io_counters()._asdict() if psutil.net_io_counters() else {},
                "connections_count": len(psutil.net_connections())
            }
            
            net_if_addrs = psutil.net_if_addrs()
            net_if_stats = psutil.net_if_stats()
            
            for interface_name, addresses in net_if_addrs.items():
                interface_info = {
                    "addresses": [],
                    "stats": net_if_stats.get(interface_name, {})
                }
                
                for addr in addresses:
                    addr_info = {
                        "family": str(addr.family),
                        "address": addr.address,
                        "netmask": addr.netmask,
                        "broadcast": addr.broadcast
                    }
                    interface_info["addresses"].append(addr_info)
                
                if hasattr(interface_info["stats"], '_asdict'):
                    interface_info["stats"] = interface_info["stats"]._asdict()
                
                network_info["interfaces"][interface_name] = interface_info
            
            return network_info
        except Exception as e:
            raise Exception(f"Error getting network info: {str(e)}")
    
    @staticmethod
    def get_process_info(limit: int = 10) -> Dict[str, Any]:
        try:
            processes = []
            
            for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent', 'status', 'create_time']):
                try:
                    proc_info = proc.info
                    proc_info['memory_mb'] = round(proc.memory_info().rss / (1024*1024), 1)
                    proc_info['create_time_str'] = datetime.fromtimestamp(proc_info['create_time']).strftime('%Y-%m-%d %H:%M:%S')
                    processes.append(proc_info)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            
            processes_by_cpu = sorted(processes, key=lambda x: x.get('cpu_percent', 0), reverse=True)
            processes_by_memory = sorted(processes, key=lambda x: x.get('memory_percent', 0), reverse=True)
            
            process_info = {
                "total_processes": len(processes),
                "top_cpu_processes": processes_by_cpu[:limit],
                "top_memory_processes": processes_by_memory[:limit],
                "process_status_summary": {}
            }
            
            status_count = {}
            for proc in processes:
                status = proc.get('status', 'unknown')
                status_count[status] = status_count.get(status, 0) + 1
            
            process_info["process_status_summary"] = status_count
            
            return process_info
        except Exception as e:
            raise Exception(f"Error getting process info: {str(e)}")

class RemoteSystemMonitorServer:
    def __init__(self):
        self.tools = {
            "get_system_overview": self.get_system_overview,
            "monitor_cpu_usage": self.monitor_cpu_usage,
            "monitor_memory_usage": self.monitor_memory_usage,
            "monitor_disk_usage": self.monitor_disk_usage,
            "monitor_network_stats": self.monitor_network_stats,
            "get_top_processes": self.get_top_processes,
            "system_monitor_info": self.system_monitor_info
        }
    
    def create_response(self, request_id: str, result: Any = None, error: Any = None):
        response = {
            "jsonrpc": "2.0",
            "id": request_id
        }
        
        if error:
            response["error"] = {
                "code": -32000,
                "message": str(error)
            }
        else:
            response["result"] = result
        
        return response
    
    def create_text_content(self, text: str):
        return {
            "type": "text",
            "text": text
        }
    
    def create_tool_result(self, content_list: List[Dict], is_error: bool = False):
        return {
            "content": content_list,
            "isError": is_error
        }
    
    async def get_system_overview(self, arguments: Dict) -> Dict:
        try:
            system_info = {
                "platform": platform.system(),
                "platform_release": platform.release(),
                "platform_version": platform.version(),
                "architecture": platform.architecture()[0],
                "machine": platform.machine(),
                "processor": platform.processor(),
                "hostname": socket.gethostname(),
                "boot_time": datetime.fromtimestamp(psutil.boot_time()).strftime('%Y-%m-%d %H:%M:%S')
            }
            
            cpu_info = SystemMonitor.get_cpu_info()
            memory_info = SystemMonitor.get_memory_info()
            disk_info = SystemMonitor.get_disk_info()
            network_info = SystemMonitor.get_network_info()
            
            uptime = datetime.now() - datetime.fromtimestamp(psutil.boot_time())
            
            summary = f"""REMOTE SYSTEM OVERVIEW
=====================================
Server: Remote MCP System Monitor
Scan Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Hostname: {system_info['hostname']}
Platform: {system_info['platform']} {system_info['platform_release']}
Architecture: {system_info['architecture']}
Boot Time: {system_info['boot_time']}
Uptime: {str(uptime).split('.')[0]}

SYSTEM RESOURCES:
=================
CPU Usage: {cpu_info['usage_percent']}% ({cpu_info['core_count']} cores, {cpu_info['logical_cores']} logical)
Memory Usage: {memory_info['usage_percent']}% ({memory_info['used_gb']:.1f}GB / {memory_info['total_gb']:.1f}GB)
Swap Usage: {memory_info['swap_percent']}% ({memory_info['swap_used_gb']:.1f}GB / {memory_info['swap_total_gb']:.1f}GB)

DISK USAGE:
==========="""
            
            for partition in disk_info['partitions']:
                summary += f"""
{partition['device']} ({partition['mountpoint']})
   Used: {partition['usage_percent']}% ({partition['used_gb']:.1f}GB / {partition['total_gb']:.1f}GB)
   Free: {partition['free_gb']:.1f}GB
   Filesystem: {partition['filesystem']}"""
            
            summary += f"""

NETWORK STATUS:
===============
Active Interfaces: {len(network_info['interfaces'])}
Total Connections: {network_info['connections_count']}"""
            
            if network_info['io_counters']:
                io = network_info['io_counters']
                summary += f"""
Bytes Sent: {io.get('bytes_sent', 0) / (1024*1024):.1f} MB
Bytes Received: {io.get('bytes_recv', 0) / (1024*1024):.1f} MB
Packets Sent: {io.get('packets_sent', 0)}
Packets Received: {io.get('packets_recv', 0)}"""
            
            alerts = []
            if cpu_info['usage_percent'] > 80:
                alerts.append(f"High CPU usage: {cpu_info['usage_percent']}%")
            if memory_info['usage_percent'] > 85:
                alerts.append(f"High memory usage: {memory_info['usage_percent']}%")
            
            for partition in disk_info['partitions']:
                if partition['usage_percent'] > 90:
                    alerts.append(f"Low disk space on {partition['device']}: {partition['usage_percent']}%")
            
            if alerts:
                summary += f"\n\nSYSTEM ALERTS:\n=============="
                for alert in alerts:
                    summary += f"\n{alert}"
            else:
                summary += f"\n\nSYSTEM STATUS: All systems operating normally"
            
            summary += f"\n\n[REMOTE MONITOR] System overview completed successfully!"
            
            text_content = self.create_text_content(summary)
            return self.create_tool_result([text_content])
            
        except Exception as e:
            error_msg = f"[REMOTE MONITOR ERROR] Error getting system overview: {str(e)}"
            text_content = self.create_text_content(error_msg)
            return self.create_tool_result([text_content], is_error=True)
    
    async def monitor_cpu_usage(self, arguments: Dict) -> Dict:
        try:
            cpu_info = SystemMonitor.get_cpu_info()
            
            summary = f"""REMOTE CPU MONITORING
============================
Server: Remote MCP System Monitor
Scan Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

CPU OVERVIEW:
=============
Overall Usage: {cpu_info['usage_percent']}%
Physical Cores: {cpu_info['core_count']}
Logical Cores: {cpu_info['logical_cores']}"""
            
            if cpu_info['frequency']:
                freq = cpu_info['frequency']
                summary += f"""
Current Frequency: {freq['current']:.1f} MHz
Max Frequency: {freq['max']:.1f} MHz
Min Frequency: {freq['min']:.1f} MHz"""
            
            summary += f"\n\nPER-CORE USAGE:\n==============="
            for i, usage in enumerate(cpu_info['per_core_usage']):
                bar_length = int(usage / 5)
                bar = "█" * bar_length + "░" * (20 - bar_length)
                summary += f"\nCore {i+1:2d}: {usage:5.1f}% [{bar}]"
            
            if cpu_info['load_average']:
                load_avg = cpu_info['load_average']
                summary += f"""

LOAD AVERAGE:
=============
1 minute:  {load_avg[0]:.2f}
5 minutes: {load_avg[1]:.2f}
15 minutes: {load_avg[2]:.2f}"""
            
            if cpu_info['usage_percent'] > 90:
                status = "CRITICAL - Very High CPU Usage"
            elif cpu_info['usage_percent'] > 70:
                status = "WARNING - High CPU Usage"
            elif cpu_info['usage_percent'] > 50:
                status = "MODERATE - Medium CPU Usage"
            else:
                status = "NORMAL - Low CPU Usage"
            
            summary += f"\n\nCPU STATUS: {status}\n"
            summary += f"[REMOTE MONITOR] CPU monitoring completed!"
            
            text_content = self.create_text_content(summary)
            return self.create_tool_result([text_content])
            
        except Exception as e:
            error_msg = f"[REMOTE MONITOR ERROR] Error monitoring CPU: {str(e)}"
            text_content = self.create_text_content(error_msg)
            return self.create_tool_result([text_content], is_error=True)
    
    async def monitor_memory_usage(self, arguments: Dict) -> Dict:
        try:
            memory_info = SystemMonitor.get_memory_info()
            
            memory_bar_length = int(memory_info['usage_percent'] / 5)
            memory_bar = "█" * memory_bar_length + "░" * (20 - memory_bar_length)
            
            swap_bar_length = int(memory_info['swap_percent'] / 5) if memory_info['swap_percent'] > 0 else 0
            swap_bar = "█" * swap_bar_length + "░" * (20 - swap_bar_length)
            
            summary = f"""REMOTE MEMORY MONITORING
===============================
Server: Remote MCP System Monitor
Scan Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

PHYSICAL MEMORY:
================
Usage: {memory_info['usage_percent']:5.1f}% [{memory_bar}]
Total: {memory_info['total_gb']:8.2f} GB
Used:  {memory_info['used_gb']:8.2f} GB
Free:  {memory_info['free_gb']:8.2f} GB
Available: {memory_info['available_gb']:8.2f} GB

SWAP MEMORY:
============"""
            
            if memory_info['swap_total_gb'] > 0:
                summary += f"""
Usage: {memory_info['swap_percent']:5.1f}% [{swap_bar}]
Total: {memory_info['swap_total_gb']:8.2f} GB
Used:  {memory_info['swap_used_gb']:8.2f} GB
Free:  {memory_info['swap_total_gb'] - memory_info['swap_used_gb']:8.2f} GB"""
            else:
                summary += f"\nNo swap memory configured"
            
            if memory_info['usage_percent'] > 90:
                status = "CRITICAL - Very High Memory Usage"
                recommendation = "Consider freeing memory or adding more RAM"
            elif memory_info['usage_percent'] > 80:
                status = "WARNING - High Memory Usage"
                recommendation = "Monitor applications and consider optimization"
            elif memory_info['usage_percent'] > 60:
                status = "MODERATE - Medium Memory Usage"
                recommendation = "System running normally"
            else:
                status = "NORMAL - Low Memory Usage"
                recommendation = "Plenty of memory available"
            
            summary += f"""

MEMORY STATUS: {status}
Recommendation: {recommendation}

[REMOTE MONITOR] Memory monitoring completed!"""
            
            text_content = self.create_text_content(summary)
            return self.create_tool_result([text_content])
            
        except Exception as e:
            error_msg = f"[REMOTE MONITOR ERROR] Error monitoring memory: {str(e)}"
            text_content = self.create_text_content(error_msg)
            return self.create_tool_result([text_content], is_error=True)
    
    async def monitor_disk_usage(self, arguments: Dict) -> Dict:
        try:
            disk_info = SystemMonitor.get_disk_info()
            
            summary = f"""REMOTE DISK MONITORING
=============================
Server: Remote MCP System Monitor
Scan Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

DISK PARTITIONS:
================"""
            
            total_disk_space = 0
            total_used_space = 0
            alerts = []
            
            for partition in disk_info['partitions']:
                usage_bar_length = int(partition['usage_percent'] / 5)
                usage_bar = "█" * usage_bar_length + "░" * (20 - usage_bar_length)
                
                status_indicator = "CRITICAL" if partition['usage_percent'] > 90 else "WARNING" if partition['usage_percent'] > 80 else "OK"
                
                summary += f"""

{status_indicator} {partition['device']} -> {partition['mountpoint']}
   Usage: {partition['usage_percent']:5.1f}% [{usage_bar}]
   Total: {partition['total_gb']:8.2f} GB
   Used:  {partition['used_gb']:8.2f} GB  
   Free:  {partition['free_gb']:8.2f} GB
   FS Type: {partition['filesystem']}"""
                
                total_disk_space += partition['total_gb']
                total_used_space += partition['used_gb']
                
                if partition['usage_percent'] > 90:
                    alerts.append(f"CRITICAL: {partition['device']} at {partition['usage_percent']}%")
                elif partition['usage_percent'] > 80:
                    alerts.append(f"WARNING: {partition['device']} at {partition['usage_percent']}%")
            
            total_usage_percent = (total_used_space / total_disk_space * 100) if total_disk_space > 0 else 0
            
            summary += f"""

TOTAL STORAGE SUMMARY:
======================
Total Space: {total_disk_space:.2f} GB
Total Used:  {total_used_space:.2f} GB
Total Free:  {total_disk_space - total_used_space:.2f} GB
Overall Usage: {total_usage_percent:.1f}%"""
            
            if disk_info['total_disk_io']:
                io = disk_info['total_disk_io']
                summary += f"""

DISK I/O STATISTICS:
====================
Read Count:  {io.get('read_count', 0):,}
Write Count: {io.get('write_count', 0):,}
Bytes Read:  {io.get('read_bytes', 0) / (1024**3):.2f} GB
Bytes Written: {io.get('write_bytes', 0) / (1024**3):.2f} GB"""
            
            if alerts:
                summary += f"\n\nDISK ALERTS:\n============"
                for alert in alerts:
                    summary += f"\n{alert}"
            else:
                summary += f"\n\nAll disks have adequate free space"
            
            summary += f"\n\n[REMOTE MONITOR] Disk monitoring completed!"
            
            text_content = self.create_text_content(summary)
            return self.create_tool_result([text_content])
            
        except Exception as e:
            error_msg = f"[REMOTE MONITOR ERROR] Error monitoring disk: {str(e)}"
            text_content = self.create_text_content(error_msg)
            return self.create_tool_result([text_content], is_error=True)
    
    async def monitor_network_stats(self, arguments: Dict) -> Dict:
        try:
            network_info = SystemMonitor.get_network_info()
            
            summary = f"""REMOTE NETWORK MONITORING
===============================
Server: Remote MCP System Monitor
Scan Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

NETWORK OVERVIEW:
=================
Active Interfaces: {len(network_info['interfaces'])}
Active Connections: {network_info['connections_count']}"""
            
            if network_info['io_counters']:
                io = network_info['io_counters']
                summary += f"""

GLOBAL NETWORK I/O:
===================
Bytes Sent:     {io.get('bytes_sent', 0) / (1024**2):10.2f} MB
Bytes Received: {io.get('bytes_recv', 0) / (1024**2):10.2f} MB
Packets Sent:   {io.get('packets_sent', 0):10,}
Packets Recv:   {io.get('packets_recv', 0):10,}
Errors In:      {io.get('errin', 0):10,}
Errors Out:     {io.get('errout', 0):10,}
Drops In:       {io.get('dropin', 0):10,}
Drops Out:      {io.get('dropout', 0):10,}"""
            
            summary += f"\n\nNETWORK INTERFACES:\n==================="
            
            for interface_name, interface_info in network_info['interfaces'].items():
                if interface_name.startswith(('lo', 'loopback')):
                    continue
                
                summary += f"\n\n{interface_name}:"
                
                stats = interface_info.get('stats', {})
                if stats:
                    is_up = stats.get('isup', False)
                    status = "UP" if is_up else "DOWN"
                    summary += f"\n   Status: {status}"
                    
                    if 'speed' in stats and stats['speed'] > 0:
                        summary += f"\n   Speed: {stats['speed']} Mbps"
                    
                    if 'mtu' in stats:
                        summary += f"\n   MTU: {stats['mtu']} bytes"
                
                addresses = interface_info.get('addresses', [])
                ip_addresses = [addr['address'] for addr in addresses if addr.get('family') == 'AddressFamily.AF_INET']
                if ip_addresses:
                    summary += f"\n   IP Addresses: {', '.join(ip_addresses)}"
            
            total_bytes = network_info['io_counters'].get('bytes_sent', 0) + network_info['io_counters'].get('bytes_recv', 0) if network_info['io_counters'] else 0
            total_mb = total_bytes / (1024**2)
            
            if total_mb > 1000:
                activity_status = "HIGH - Heavy network activity"
            elif total_mb > 100:
                activity_status = "MODERATE - Normal network activity"
            elif total_mb > 10:
                activity_status = "LOW - Light network activity"
            else:
                activity_status = "MINIMAL - Very low network activity"
            
            summary += f"""

NETWORK ACTIVITY: {activity_status}
Total Data Transfer: {total_mb:.2f} MB

[REMOTE MONITOR] Network monitoring completed!"""
            
            text_content = self.create_text_content(summary)
            return self.create_tool_result([text_content])
            
        except Exception as e:
            error_msg = f"[REMOTE MONITOR ERROR] Error monitoring network: {str(e)}"
            text_content = self.create_text_content(error_msg)
            return self.create_tool_result([text_content], is_error=True)
    
    async def get_top_processes(self, arguments: Dict) -> Dict:
        try:
            limit = arguments.get("limit", 10)
            process_info = SystemMonitor.get_process_info(limit)
            
            summary = f"""REMOTE PROCESS MONITORING
================================
Server: Remote MCP System Monitor
Scan Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

PROCESS SUMMARY:
================
Total Processes: {process_info['total_processes']}"""
            
            if process_info['process_status_summary']:
                summary += f"\n\nPROCESS STATUS SUMMARY:\n======================="
                for status, count in process_info['process_status_summary'].items():
                    summary += f"\n{status.title():12}: {count:4d} processes"
            
            summary += f"\n\nTOP {limit} PROCESSES BY CPU:\n==========================="
            summary += f"\n{'PID':>8} {'CPU%':>6} {'MEM%':>6} {'MEM(MB)':>8} {'NAME':<20} {'STATUS'}"
            summary += f"\n{'-'*8} {'-'*6} {'-'*6} {'-'*8} {'-'*20} {'-'*10}"
            
            for proc in process_info['top_cpu_processes']:
                summary += f"\n{proc.get('pid', 0):8d} {proc.get('cpu_percent', 0):6.1f} {proc.get('memory_percent', 0):6.1f} {proc.get('memory_mb', 0):8.1f} {proc.get('name', 'unknown')[:20]:<20} {proc.get('status', 'unknown')[:10]}"
            
            summary += f"\n\nTOP {limit} PROCESSES BY MEMORY:\n=============================="
            summary += f"\n{'PID':>8} {'MEM%':>6} {'MEM(MB)':>8} {'CPU%':>6} {'NAME':<20} {'STARTED'}"
            summary += f"\n{'-'*8} {'-'*6} {'-'*8} {'-'*6} {'-'*20} {'-'*19}"
            
            for proc in process_info['top_memory_processes']:
                create_time = proc.get('create_time_str', 'unknown')[:19]
                summary += f"\n{proc.get('pid', 0):8d} {proc.get('memory_percent', 0):6.1f} {proc.get('memory_mb', 0):8.1f} {proc.get('cpu_percent', 0):6.1f} {proc.get('name', 'unknown')[:20]:<20} {create_time}"
            
            top_cpu_usage = sum(proc.get('cpu_percent', 0) for proc in process_info['top_cpu_processes'][:5])
            top_memory_usage = sum(proc.get('memory_percent', 0) for proc in process_info['top_memory_processes'][:5])
            
            summary += f"""

RESOURCE ANALYSIS:
==================
Top 5 CPU consumers: {top_cpu_usage:.1f}% total
Top 5 Memory consumers: {top_memory_usage:.1f}% total
Running Processes: {process_info['process_status_summary'].get('running', 0)}
Sleeping Processes: {process_info['process_status_summary'].get('sleeping', 0)}"""
            
            if top_cpu_usage > 80:
                summary += f"\nHigh CPU usage by top processes - consider optimization"
            if top_memory_usage > 70:
                summary += f"\nHigh memory usage by top processes - monitor for memory leaks"
            
            summary += f"\n\n[REMOTE MONITOR] Process monitoring completed!"
            
            text_content = self.create_text_content(summary)
            return self.create_tool_result([text_content])
            
        except Exception as e:
            error_msg = f"[REMOTE MONITOR ERROR] Error monitoring processes: {str(e)}"
            text_content = self.create_text_content(error_msg)
            return self.create_tool_result([text_content], is_error=True)
    
    async def system_monitor_info(self, arguments: Dict) -> Dict:
        try:
            server_info = f"""REMOTE SYSTEM MONITOR SERVER
===================================
Server Name: System Monitor Remote Server
Version: 1.0.0
Location: Cloud-hosted Remote Server
Status: Active and Monitoring
Current Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Framework: Flask + Python + psutil

MONITORING CAPABILITIES:
========================
get_system_overview
   Complete system status and health check
   CPU, memory, disk, and network overview
   System alerts and recommendations

monitor_cpu_usage
   Detailed CPU utilization monitoring
   Per-core usage statistics
   Load average and frequency information

monitor_memory_usage
   Physical and swap memory monitoring  
   Memory usage visualization
   Status alerts and recommendations

monitor_disk_usage
   Disk partition monitoring
   Storage utilization and I/O statistics
   Free space alerts

monitor_network_stats
   Network interface monitoring
   Bandwidth and connection statistics
   Data transfer analysis

get_top_processes
   Resource-intensive process identification
   CPU and memory usage ranking
   Process status summary

system_monitor_info
   Display server capabilities and information
   System specifications overview

KEY FEATURES:
=============
Real-time system monitoring
Cross-platform compatibility (Linux, Windows, macOS)
Detailed resource utilization reports
System health alerts and recommendations
Process monitoring and analysis
Network activity tracking
Disk usage and I/O monitoring
Memory management insights
Professional formatted reports

TECHNICAL SPECIFICATIONS:
=========================
Libraries: psutil, platform, socket, datetime
Python Version: 3.7+
Protocol: JSON-RPC 2.0 over HTTP
Communication: REST API endpoints
CORS: Enabled for cross-origin requests
Performance: Optimized for real-time monitoring
Error Handling: Comprehensive exception management
Refresh Rate: On-demand monitoring

MONITORING TIPS:
================
Use get_system_overview for quick health checks
Monitor CPU usage regularly for performance optimization
Set up alerts for high disk usage (>90%)
Track memory usage to prevent system slowdowns
Monitor network stats for connectivity issues
Use process monitoring to identify resource hogs

SYSTEM REQUIREMENTS:
====================
Python 3.7 or higher
psutil library for system monitoring
Flask for web server functionality
Administrative privileges for full system access
Network connectivity for remote monitoring

[REMOTE MONITOR] Server information retrieved successfully!
Ready to monitor your system resources!"""
            
            text_content = self.create_text_content(server_info)
            return self.create_tool_result([text_content])
            
        except Exception as e:
            error_msg = f"[REMOTE MONITOR ERROR] Error getting server info: {str(e)}"
            text_content = self.create_text_content(error_msg)
            return self.create_tool_result([text_content], is_error=True)

remote_system_monitor = RemoteSystemMonitorServer()

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        "status": "healthy",
        "server": "Remote MCP System Monitor Server",
        "version": "1.0.0",
        "timestamp": datetime.now().isoformat(),
        "uptime": "Server is running normally",
        "system_status": {
            "cpu_usage": psutil.cpu_percent(),
            "memory_usage": psutil.virtual_memory().percent,
            "disk_usage": psutil.disk_usage('/').percent if platform.system() != 'Windows' else psutil.disk_usage('C:').percent
        }
    })

@app.route('/mcp/tools/list', methods=['GET'])
def list_tools():
    tools = [
        {
            "name": "get_system_overview",
            "description": "Get a comprehensive overview of system status including CPU, memory, disk, and network",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        {
            "name": "monitor_cpu_usage",
            "description": "Monitor detailed CPU usage including per-core statistics and load average",
            "inputSchema": {
                "type": "object", 
                "properties": {},
                "required": []
            }
        },
        {
            "name": "monitor_memory_usage",
            "description": "Monitor physical and swap memory usage with detailed statistics",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        {
            "name": "monitor_disk_usage",
            "description": "Monitor disk usage for all partitions including I/O statistics",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        {
            "name": "monitor_network_stats",
            "description": "Monitor network interfaces and bandwidth statistics",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        {
            "name": "get_top_processes",
            "description": "Get top processes by CPU and memory usage",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Number of top processes to return",
                        "default": 10,
                        "minimum": 1,
                        "maximum": 50
                    }
                },
                "required": []
            }
        },
        {
            "name": "system_monitor_info", 
            "description": "Get information about the system monitor server capabilities",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    ]
    
    return jsonify({"tools": tools})

@app.route('/mcp/tools/call', methods=['POST'])
def call_tool():
    try:
        data = request.get_json()
        
        if not data:
            error_response = {
                "jsonrpc": "2.0",
                "id": "unknown",
                "error": {"code": -32600, "message": "Invalid Request - No JSON data received"}
            }
            return jsonify(error_response), 400
        
        tool_name = data.get("name")
        arguments = data.get("arguments", {})
        request_id = data.get("id", str(uuid.uuid4()))
        
        if not tool_name:
            error_response = remote_system_monitor.create_response(
                request_id, 
                error="Tool name is required in the 'name' field"
            )
            return jsonify(error_response), 400
        
        if tool_name not in remote_system_monitor.tools:
            available_tools = list(remote_system_monitor.tools.keys())
            error_msg = f"Unknown tool: {tool_name}. Available tools: {available_tools}"
            error_response = remote_system_monitor.create_response(request_id, error=error_msg)
            return jsonify(error_response), 400
        
        try:            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                result = loop.run_until_complete(remote_system_monitor.tools[tool_name](arguments))
            finally:
                loop.close()
            
            if result is None:
                result = {
                    "content": [{"type": "text", "text": "Monitoring tool completed but returned no result"}], 
                    "isError": True
                }
            
            response = remote_system_monitor.create_response(request_id, result)
            return jsonify(response), 200
            
        except Exception as tool_error:
            error_msg = f"Monitoring tool execution failed: {str(tool_error)}"
            error_response = remote_system_monitor.create_response(request_id, error=error_msg)
            return jsonify(error_response), 500
            
    except Exception as e:
        error_msg = f"Server error: {str(e)}"
        error_response = {
            "jsonrpc": "2.0",
            "id": data.get("id", "unknown") if 'data' in locals() and data else "unknown",
            "error": {"code": -32000, "message": error_msg}
        }
        return jsonify(error_response), 500

@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "error": "Endpoint not found",
        "available_endpoints": ["/", "/health", "/mcp/tools/list", "/mcp/tools/call"]
    }), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({
        "error": "Internal server error", 
        "message": "An unexpected error occurred on the server"
    }), 500

if __name__ == '__main__':
    try:
        app.run(host='0.0.0.0', port=5000, debug=False)
    except Exception as e:
        traceback.print_exc()