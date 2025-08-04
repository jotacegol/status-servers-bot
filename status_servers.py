#!/usr/bin/env python3
"""
🤖 IOSoccer Bot - Versión Portable
⚽ Bot de Discord para monitorear servidores IOSoccer
🔧 Configuración automática desde config.txt
📦 Compatible con Windows Server 2012 R2+
"""

import os
import sys
import asyncio
import socket
import struct
import json
import time
import logging
from datetime import datetime
from pathlib import Path

# Verificar e importar dependencias
try:
    import discord
    from discord.ext import commands
    from rcon import Client
except ImportError as e:
    print("❌ Error: Falta instalar dependencias")
    print(f"📍 Error específico: {e}")
    print("🔧 Solución: Ejecuta 'install_dependencies.bat'")
    input("Presiona Enter para cerrar...")
    sys.exit(1)

# ============================================
# 🔧 CONFIGURACIÓN AUTOMÁTICA
# ============================================

def load_config():
    """Carga configuración desde config.txt"""
    config = {}
    config_file = Path("config.txt")
    
    if not config_file.exists():
        print("❌ Error: config.txt no encontrado")
        print("🔧 Solución: Ejecuta setup.py primero")
        input("Presiona Enter para cerrar...")
        sys.exit(1)
    
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip().strip("'\"")
                    
                    # Convertir listas de puertos
                    if 'RCON_PORTS' in key:
                        try:
                            # Remover corchetes y convertir a lista de enteros
                            value = value.strip('[]')
                            value = [int(x.strip()) for x in value.split(',')]
                        except:
                            value = [27018, 27015, 27019, 27020, 27021]
                    elif 'PORT' in key and key != 'RCON_PORTS':
                        try:
                            value = int(value)
                        except:
                            value = 27018
                    
                    config[key] = value
        
        # Validar configuración mínima
        required_keys = ['DISCORD_TOKEN', 'RCON_PASSWORD']
        missing_keys = [key for key in required_keys if not config.get(key) or config.get(key) == 'TU_TOKEN_AQUI' or config.get(key) == 'TU_RCON_PASSWORD_AQUI']
        
        if missing_keys:
            print("❌ Error: Configuración incompleta")
            print(f"📍 Faltan configurar: {', '.join(missing_keys)}")
            print("🔧 Solución: Edita config.txt con tus datos reales")
            input("Presiona Enter para cerrar...")
            sys.exit(1)
        
        print("✅ Configuración cargada correctamente")
        return config
        
    except Exception as e:
        print(f"❌ Error leyendo config.txt: {e}")
        input("Presiona Enter para cerrar...")
        sys.exit(1)

# Cargar configuración
CONFIG = load_config()

# Extraer configuración
TOKEN = CONFIG['DISCORD_TOKEN']
RCON_PASSWORD = CONFIG['RCON_PASSWORD']

# Configurar servidores dinámicamente
SERVERS = []

# Servidor 1
if CONFIG.get('SERVER_1_IP'):
    SERVERS.append({
        'name': CONFIG.get('SERVER_1_NAME', 'Servidor IOSoccer #1'),
        'ip': CONFIG['SERVER_1_IP'],
        'port': CONFIG.get('SERVER_1_PORT', 27029),
        'rcon_ports': CONFIG.get('SERVER_1_RCON_PORTS', [27029, 27026, 27030, 27031, 27032]),
    })

# Servidor 2
if CONFIG.get('SERVER_2_IP'):
    SERVERS.append({
        'name': CONFIG.get('SERVER_2_NAME', 'Servidor IOSoccer #2'),
        'ip': CONFIG['SERVER_2_IP'],
        'port': CONFIG.get('SERVER_2_PORT', 27031),
        'rcon_ports': CONFIG.get('SERVER_2_RCON_PORTS', [27031, 27028, 27032, 27033, 27034]),
    })

if not SERVERS:
    print("❌ Error: No se configuraron servidores")
    print("🔧 Solución: Configura al menos SERVER_1_IP en config.txt")
    input("Presiona Enter para cerrar...")
    sys.exit(1)

# ============================================
# 📊 CONFIGURACIÓN DE LOGGING
# ============================================

log_level = CONFIG.get('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# ============================================
# 🎮 COMANDOS IOSOCCER
# ============================================

IOSOCCER_COMMANDS = [
    'status', 'version', 'echo "test"',
    'sv_matchinfojson', 'ios_match_info', 'ios_score', 
    'ios_time', 'ios_players', 'users', 'listplayers', 
    'players', 'stats',
]

# ============================================
# 🤖 CONFIGURACIÓN DEL BOT
# ============================================

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# ============================================
# 📊 CLASES DE DATOS
# ============================================

class ServerInfo:
    """Clase para almacenar información del servidor"""
    def __init__(self, name, status, players=0, max_players=0, map_name="N/A", 
                 match_info=None, basic_info=None):
        self.name = name
        self.status = status
        self.players = players
        self.max_players = max_players
        self.map_name = map_name
        self.match_info = match_info
        self.basic_info = basic_info

class A2SQuery:
    """Clase para consultas A2S_INFO simplificada"""
    
    @staticmethod
    def query_server(ip, port, timeout=5):
        """Consulta información básica del servidor usando A2S_INFO"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(timeout)
            
            packet = b'\xFF\xFF\xFF\xFF\x54Source Engine Query\x00'
            sock.sendto(packet, (ip, port))
            data, addr = sock.recvfrom(4096)
            sock.close()
            
            if len(data) < 25:
                return None
                
            offset = 6
            
            # Server name
            name_end = data.find(b'\x00', offset)
            if name_end == -1: return None
            server_name = data[offset:name_end].decode('utf-8', errors='ignore')
            offset = name_end + 1
            
            # Map name
            map_end = data.find(b'\x00', offset)
            if map_end == -1: return None
            map_name = data[offset:map_end].decode('utf-8', errors='ignore')
            offset = map_end + 1
            
            # Skip folder and game
            for _ in range(2):
                end = data.find(b'\x00', offset)
                if end == -1: return None
                offset = end + 1
            
            offset += 2
            
            if offset + 1 >= len(data): return None
            players = data[offset]
            max_players = data[offset + 1]
            
            logger.info(f"✅ A2S_INFO {ip}:{port} -> {players}/{max_players} en {map_name}")
            
            return {
                'server_name': server_name,
                'map_name': map_name,
                'players': players,
                'max_players': max_players
            }
                
        except Exception as e:
            logger.error(f"❌ A2S_INFO error {ip}:{port}: {e}")
            return None

class RCONManager:
    """Manejador RCON usando rcon-client"""
    
    @staticmethod
    async def test_rcon_connection(ip, port, password, timeout=10):
        """Prueba la conexión RCON"""
        try:
            logger.info(f"🔌 Probando RCON {ip}:{port}")
            
            with Client(ip, port, passwd=password, timeout=timeout) as client:
                response = client.run('echo "RCON_TEST_OK"')
                
                if response and 'RCON_TEST_OK' in response:
                    logger.info(f"✅ RCON {ip}:{port} - Conexión exitosa")
                    return {'success': True, 'error': None, 'response': response.strip()}
                else:
                    logger.warning(f"🔶 RCON {ip}:{port} - Respuesta inesperada: {response}")
                    return {'success': True, 'error': 'Respuesta inesperada', 'response': response}
                    
        except Exception as e:
            logger.error(f"❌ RCON {ip}:{port} - Error: {e}")
            return {'success': False, 'error': str(e), 'response': None}
    
    @staticmethod
    async def execute_command(ip, port, password, command, timeout=10):
        """Ejecuta un comando RCON"""
        try:
            logger.info(f"🔄 Ejecutando: {command} en {ip}:{port}")
            
            with Client(ip, port, passwd=password, timeout=timeout) as client:
                response = client.run(command)
                
                if response and len(response.strip()) > 0:
                    logger.info(f"✅ Comando exitoso: {len(response)} caracteres")
                    return {'success': True, 'response': response.strip(), 'error': None}
                else:
                    logger.warning(f"⚠️ Sin respuesta para: {command}")
                    return {'success': False, 'response': '', 'error': 'Sin respuesta'}
                    
        except Exception as e:
            logger.error(f"❌ Error comando '{command}': {e}")
            return {'success': False, 'response': '', 'error': str(e)}
    
    @staticmethod
    async def find_working_rcon_port(server, password):
        """Encuentra el puerto RCON que funciona"""
        logger.info(f"🔍 Buscando puerto RCON funcional para {server['name']}")
        
        for port in server['rcon_ports']:
            test_result = await RCONManager.test_rcon_connection(
                server['ip'], port, password, timeout=8
            )
            
            if test_result['success']:
                logger.info(f"✅ Puerto RCON funcional encontrado: {port}")
                return {'port': port, 'success': True, 'error': None}
        
        logger.error(f"❌ No se encontró puerto RCON funcional para {server['name']}")
        return {'port': None, 'success': False, 'error': 'No hay puertos RCON funcionales'}
    
    @staticmethod
    async def get_match_info_json(server, password):
        """Obtiene información del partido usando sv_matchinfojson"""
        port_result = await RCONManager.find_working_rcon_port(server, password)
        
        if not port_result['success']:
            return {
                'success': False, 'data': None, 
                'working_port': None, 'error': port_result['error']
            }
        
        working_port = port_result['port']
        logger.info(f"🎮 Obteniendo match info JSON desde puerto {working_port}")
        
        result = await RCONManager.execute_command(
            server['ip'], working_port, password, 'sv_matchinfojson', timeout=10
        )
        
        if not result['success'] or not result['response']:
            return {
                'success': False, 'data': None, 
                'working_port': working_port, 'error': result['error'] or 'Sin respuesta JSON'
            }
        
        try:
            response = result['response'].strip()
            json_start = response.find('{')
            json_end = response.rfind('}')
            
            if json_start == -1 or json_end == -1:
                return {
                    'success': False, 'data': None, 'working_port': working_port,
                    'error': 'No se encontró JSON válido en la respuesta'
                }
            
            json_text = response[json_start:json_end+1]
            match_data = json.loads(json_text)
            
            logger.info(f"✅ JSON parseado exitosamente: {len(json_text)} caracteres")
            
            return {
                'success': True, 'data': match_data, 
                'working_port': working_port, 'error': None
            }
            
        except json.JSONDecodeError as e:
            logger.error(f"❌ Error parsing JSON: {e}")
            return {
                'success': False, 'data': None, 
                'working_port': working_port, 'error': f'Error JSON: {str(e)}'
            }

# ============================================
# 🎯 FUNCIONES DE PROCESAMIENTO
# ============================================

def parse_match_info(match_data):
    """Parsea la información del partido desde el JSON"""
    if not match_data:
        return None
    
    try:
        info = {
            'period': match_data.get('matchPeriod', 'N/A'),
            'time_display': match_data.get('matchDisplaySeconds', '0:00'),
            'time_seconds': match_data.get('matchSeconds', 0),
            'map_name': match_data.get('mapName', 'N/A'),
            'format': f"{match_data.get('matchFormat', 0)}v{match_data.get('matchFormat', 0)}",
            'players_count': match_data.get('serverPlayerCount', 0),
            'max_players': match_data.get('serverMaxPlayers', 0),
            'team_home': match_data.get('teamNameHome', 'Local'),
            'team_away': match_data.get('teamNameAway', 'Visitante'),
            'code_home': match_data.get('teamCodeHome', 'LOC'),
            'code_away': match_data.get('teamCodeAway', 'VIS'),
            'goals_home': match_data.get('matchGoalsHome', 0),
            'goals_away': match_data.get('matchGoalsAway', 0),
            'lineup_home': match_data.get('teamLineupHome', []),
            'lineup_away': match_data.get('teamLineupAway', []),
            'events': match_data.get('matchEvents', [])
        }
        return info
    except Exception as e:
        logger.error(f"❌ Error parsing match info: {e}")
        return None

def get_active_players(lineup):
    """Obtiene jugadores activos de una lineup"""
    active_players = []
    for player in lineup:
        if player.get('steamId') and player.get('name') and player.get('steamId') != 'BOT':
            active_players.append({
                'position': player.get('position', 'N/A'),
                'name': player.get('name', 'Unknown')
            })
    return active_players

def create_match_embed(server_info):
    """Crea embed detallado con información del partido"""
    if not server_info.match_info:
        embed = discord.Embed(
            title=f"⚽ {server_info.name}",
            color=0x00ff00 if "Online" in server_info.status else 0xff0000
        )
        
        server_config = next((s for s in SERVERS if s['name'] == server_info.name), None)
        connect_info = f"{server_config['ip']}:{server_config['port']}" if server_config else "N/A"
        
        if "Online" in server_info.status:
            embed.add_field(
                name="📊 Estado del Servidor",
                value=f"**🎮 Estado:** {server_info.status}\n"
                      f"**👥 Jugadores:** {server_info.players}/{server_info.max_players}\n"
                      f"**🗺️ Mapa:** {server_info.map_name}\n"
                      f"**🌐 Conectar:** `connect {connect_info};password elo`",
                inline=False
            )
        else:
            embed.add_field(
                name="❌ Servidor Offline",
                value=f"**📡 IP:** `{connect_info}`\n**Estado:** {server_info.status}",
                inline=False
            )
        return embed
    
    match_info = server_info.match_info
    
    if match_info['period'] in ['FIRST HALF', 'SECOND HALF']:
        color = 0x00ff00
    elif match_info['period'] == 'HALF TIME':
        color = 0xffa500
    else:
        color = 0x0099ff
    
    embed = discord.Embed(
        title=f"⚽ {server_info.name} - {match_info['format']}",
        color=color,
        timestamp=datetime.now()
    )
    
    score_text = f"**{match_info['team_home']} {match_info['goals_home']} - {match_info['goals_away']} {match_info['team_away']}**"
    
    embed.add_field(
        name="🏆 Marcador",
        value=f"{score_text}\n"
              f"⏱️ **{match_info['time_display']}** | 📅 **{match_info['period']}**",
        inline=False
    )
    
    server_config = next((s for s in SERVERS if s['name'] == server_info.name), None)
    connect_info = f"{server_config['ip']}:{server_config['port']}" if server_config else "N/A"
    
    embed.add_field(
        name="🎮 Información del Servidor",
        value=f"**👥 Jugadores:** {match_info['players_count']}/{match_info['max_players']}\n"
              f"**🗺️ Mapa:** {match_info['map_name']}\n"
              f"**🌐 Conectar:** `connect {connect_info};password elo`",
        inline=False
    )
    
    home_players = get_active_players(match_info['lineup_home'])
    away_players = get_active_players(match_info['lineup_away'])
    
    if home_players or away_players:
        if home_players:
            home_text = ""
            for player in home_players[:6]:
                home_text += f"**{player['position']}:** {player['name']}\n"
            if len(home_players) > 6:
                home_text += f"... y {len(home_players) - 6} más"
        else:
            home_text = "Solo bots activos"
        
        embed.add_field(
            name=f"🏠 {match_info['team_home']} ({match_info['code_home']})",
            value=home_text,
            inline=True
        )
        
        if away_players:
            away_text = ""
            for player in away_players[:6]:
                away_text += f"**{player['position']}:** {player['name']}\n"
            if len(away_players) > 6:
                away_text += f"... y {len(away_players) - 6} más"
        else:
            away_text = "Solo bots activos"
        
        embed.add_field(
            name=f"✈️ {match_info['team_away']} ({match_info['code_away']})",
            value=away_text,
            inline=True
        )
    
    if match_info['events'] and len(match_info['events']) > 0:
        events_text = ""
        for event in match_info['events'][-3:]:
            events_text += f"• {event}\n"
        
        embed.add_field(
            name="📝 Eventos Recientes",
            value=events_text,
            inline=False
        )
    
    return embed

def create_status_embed(servers_info):
    """Crea el embed de estado general de todos los servidores"""
    embed = discord.Embed(
        title="⚽ Estado Servidores IOSoccer",
        description="Información en tiempo real con Match Info JSON",
        color=0x00ff00,
        timestamp=datetime.now()
    )
    
    online_count = 0
    total_players = 0
    active_matches = 0
    
    for server_info in servers_info:
        if "Online" in server_info.status:
            online_count += 1
            total_players += server_info.players
            
            if (server_info.match_info and 
                server_info.match_info['period'] in ['FIRST HALF', 'SECOND HALF']):
                active_matches += 1
    
    summary = f"**🌐 Servidores Online:** {online_count}/{len(servers_info)}\n"
    summary += f"**👥 Jugadores Totales:** {total_players}\n"
    summary += f"**⚽ Partidos Activos:** {active_matches}"
    
    embed.add_field(
        name="📊 Resumen General",
        value=summary,
        inline=False
    )
    
    embed.set_footer(
        text=f"🔄 Actualizado | {datetime.now().strftime('%H:%M:%S')}"
    )
    
    return embed

async def get_server_info(server):
    """Obtiene información completa del servidor"""
    try:
        logger.info(f"📡 Consultando servidor: {server['name']}")
        
        a2s_info = A2SQuery.query_server(server['ip'], server['port'], timeout=6)
        
        if not a2s_info:
            return ServerInfo(name=server['name'], status="🔴 Offline")
        
        match_result = await RCONManager.get_match_info_json(server, RCON_PASSWORD)
        
        match_info = None
        if match_result['success'] and match_result['data']:
            match_info = parse_match_info(match_result['data'])
            logger.info(f"✅ Match info obtenida para {server['name']}")
        else:
            logger.warning(f"⚠️ No se pudo obtener match info: {match_result['error']}")
        
        return ServerInfo(
            name=server['name'],
            status="🟢 Online",
            players=a2s_info['players'],
            max_players=a2s_info['max_players'],
            map_name=a2s_info['map_name'],
            match_info=match_info,
            basic_info=a2s_info
        )
        
    except Exception as e:
        logger.error(f"❌ Error obteniendo info de {server['name']}: {e}")
        return ServerInfo(name=server['name'], status="🔴 Error")

# ============================================
# 🤖 EVENTOS Y COMANDOS DEL BOT
# ============================================

@bot.event
async def on_ready():
    """Bot listo"""
    logger.info(f'🤖 {bot.user.name} conectado!')
    logger.info(f'🔧 Versión portable con configuración automática')
    logger.info(f'🎮 Monitoreando {len(SERVERS)} servidores IOSoccer')
    print('='*50)
    print(f'✅ Bot iniciado correctamente')
    print(f'🎮 Servidores configurados: {len(SERVERS)}')
    print(f'📊 Usa !status para ver el estado de los servidores')
    print('='*50)

@bot.command(name='status')
async def server_status(ctx):
    """Estado de todos los servidores con información detallada de partidos"""
    loading_embed = discord.Embed(
        title="🔄 Consultando servidores...",
        description="Obteniendo información A2S + Match Info JSON",
        color=0xffff00
    )
    message = await ctx.send(embed=loading_embed)
    
    servers_info = []
    
    for i, server in enumerate(SERVERS):
        loading_embed.description = f"Analizando {server['name']} ({i+1}/{len(SERVERS)})"
        loading_embed.add_field(
            name="📡 Progreso",
            value=f"{'✅ ' * i}{'🔄 ' if i < len(SERVERS) else ''}{'⏳ ' * (len(SERVERS) - i - 1)}",
            inline=False
        )
        await message.edit(embed=loading_embed)
        
        server_info = await get_server_info(server)
        servers_info.append(server_info)
        loading_embed.clear_fields()
    
    status_embed = create_status_embed(servers_info)
    await message.edit(embed=status_embed)
    
    for server_info in servers_info:
        match_embed = create_match_embed(server_info)
        await ctx.send(embed=match_embed)

@bot.command(name='server')
async def individual_server(ctx, server_num: int = 1):
    """Información detallada de un servidor específico"""
    if server_num < 1 or server_num > len(SERVERS):
        await ctx.send(f"❌ Servidor inválido. Usa 1-{len(SERVERS)}")
        return
    
    server = SERVERS[server_num - 1]
    
    loading_embed = discord.Embed(
        title=f"🔄 Consultando {server['name']}...",
        description="Obteniendo información detallada",
        color=0xffff00
    )
    message = await ctx.send(embed=loading_embed)
    
    server_info = await get_server_info(server)
    match_embed = create_match_embed(server_info)
    
    await message.edit(embed=match_embed)

@bot.command(name='rcon')
async def test_rcon_simple(ctx, server_num: int = 1, *, command: str = "status"):
    """Prueba comando RCON específico"""
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ Solo administradores")
        return
    
    if server_num < 1 or server_num > len(SERVERS):
        await ctx.send(f"❌ Servidor inválido. Usa 1-{len(SERVERS)}")
        return
    
    server = SERVERS[server_num - 1]
    
    embed = discord.Embed(
        title=f"🔧 Test RCON - {server['name']}",
        description=f"Comando: `{command}`",
        color=0x00aaff
    )
    
    port_result = await RCONManager.find_working_rcon_port(server, RCON_PASSWORD)
    
    if not port_result['success']:
        embed.add_field(
            name="❌ Error",
            value=f"No se pudo conectar RCON: {port_result['error']}",
            inline=False
        )
        await ctx.send(embed=embed)
        return
    
    result = await RCONManager.execute_command(
        server['ip'], port_result['port'], RCON_PASSWORD, command
    )
    
    if result['success']:
        response = result['response']
        display_response = response[:1500] if len(response) > 1500 else response
        if len(response) > 1500:
            display_response += "\n... (truncado)"
        
        embed.add_field(
            name=f"✅ Respuesta (Puerto {port_result['port']})",
            value=f"```\n{display_response}\n```",
            inline=False
        )
    else:
        embed.add_field(
            name="❌ Error",
            value=result['error'],
            inline=False
        )
    
    await ctx.send(embed=embed)

@bot.command(name='matchjson')
async def get_match_json(ctx, server_num: int = 1):
    """Obtiene el JSON completo del partido"""
    if server_num < 1 or server_num > len(SERVERS):
        await ctx.send(f"❌ Servidor inválido. Usa 1-{len(SERVERS)}")
        return
    
    server = SERVERS[server_num - 1]
    
    loading_embed = discord.Embed(
        title=f"🔄 Obteniendo Match JSON...",
        description=f"Servidor: {server['name']}",
        color=0xffff00
    )
    message = await ctx.send(embed=loading_embed)
    
    match_result = await RCONManager.get_match_info_json(server, RCON_PASSWORD)
    
    if not match_result['success']:
        error_embed = discord.Embed(
            title="❌ Error obteniendo Match JSON",
            description=f"**Error:** {match_result['error']}",
            color=0xff0000
        )
        await message.edit(embed=error_embed)
        return
    
    json_text = json.dumps(match_result['data'], indent=2, ensure_ascii=False)
    
    embed = discord.Embed(
        title=f"📋 Match JSON - {server['name']}",
        description=f"Puerto RCON: {match_result['working_port']}",
        color=0x00ff00
    )
    
    if len(json_text) > 1900:
        json_text = json_text[:1900] + "\n... (truncado)"
    
    embed.add_field(
        name="📄 JSON Data",
        value=f"```json\n{json_text}\n```",
        inline=False
    )
    
    await message.edit(embed=embed)

@bot.command(name='ping')
async def ping_command(ctx):
    """Latencia del bot"""
    latency = round(bot.latency * 1000)
    embed = discord.Embed(
        title="🏓 Pong!",
        description=f"Latencia: **{latency}ms**",
        color=0x00ff00 if latency < 100 else 0xffff00 if latency < 200 else 0xff0000
    )
    await ctx.send(embed=embed)

@bot.command(name='config')
async def show_config(ctx):
    """Muestra la configuración actual del bot"""
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ Solo administradores")
        return
    
    embed = discord.Embed(
        title="🔧 Configuración Actual",
        color=0x0099ff
    )
    
    embed.add_field(
        name="🤖 Bot",
        value=f"**Token:** {TOKEN[:20]}...{TOKEN[-10:]}\n"
              f"**RCON Password:** {'*' * len(RCON_PASSWORD)}\n"
              f"**Log Level:** {CONFIG.get('LOG_LEVEL', 'INFO')}",
        inline=False
    )
    
    servers_text = ""
    for i, server in enumerate(SERVERS, 1):
        servers_text += f"**{i}.** {server['name']}\n"
        servers_text += f"    IP: {server['ip']}:{server['port']}\n"
        servers_text += f"    RCON: {server['rcon_ports']}\n\n"
    
    embed.add_field(
        name="🎮 Servidores",
        value=servers_text,
        inline=False
    )
    
    await ctx.send(embed=embed)

@bot.command(name='help')
async def help_command(ctx):
    """Ayuda del bot"""
    embed = discord.Embed(
        title="🤖 IOSoccer Bot - Versión Portable",
        description="Bot con configuración automática desde config.txt",
        color=0x0099ff
    )
    
    commands_help = [
        ("🎮 !status", "Estado completo de todos los servidores"),
        ("⚽ !server [1-N]", "Información detallada de un servidor"),
        ("📋 !matchjson [1-N]", "JSON completo del partido en curso"),
        ("🔧 !rcon [1-N] [comando]", "(Admin) Ejecuta comando RCON"),
        ("🔧 !config", "(Admin) Muestra configuración actual"),
        ("🏓 !ping", "Latencia del bot"),
    ]
    
    for name, description in commands_help:
        embed.add_field(name=name, value=description, inline=False)
    
    embed.add_field(
        name="📁 Archivos",
        value="• `config.txt` - Configuración del bot\n• `bot.log` - Logs del bot\n• `README.md` - Documentación",
        inline=False
    )
    
    embed.set_footer(text="🔧 Versión portable - Configuración desde config.txt")
    
    await ctx.send(embed=embed)

@bot.event
async def on_command_error(ctx, error):
    """Manejo de errores"""
    if isinstance(error, commands.CommandNotFound):
        await ctx.send("❌ Comando no encontrado. Usa `!help`")
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ No tienes permisos para este comando")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌ Argumento inválido. Usa `!help` para ver la sintaxis correcta")
    else:
        logger.error(f"Error: {error}")
        await ctx.send(f"❌ Error: {str(error)}")

# ============================================
# 🚀 INICIALIZACIÓN
# ============================================

def main():
    """Función principal"""
    print("🚀 Iniciando IOSoccer Bot - Versión Portable")
    print("🔧 Configuración automática desde config.txt")
    print("📦 Compatible con Windows Server 2012 R2+")
    print("="*60)
    
    try:
        # Mostrar configuración cargada
        print(f"✅ Token configurado: {TOKEN[:20]}...{TOKEN[-10:]}")
        print(f"✅ RCON Password: {'*' * len(RCON_PASSWORD)}")
        print(f"✅ Servidores configurados: {len(SERVERS)}")
        for i, server in enumerate(SERVERS, 1):
            print(f"   {i}. {server['name']} - {server['ip']}:{server['port']}")
        print("="*60)
        
        # Iniciar bot
        bot.run(TOKEN)
        
    except discord.LoginFailure:
        print("❌ Error: Token de Discord inválido")
        print("🔧 Solución: Verifica el token en config.txt")
        input("Presiona Enter para cerrar...")
    except Exception as e:
        logger.error(f"❌ Error fatal: {e}")
        print(f"❌ Error: {e}")
        input("Presiona Enter para cerrar...")

if __name__ == "__main__":
    main()
