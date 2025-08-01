import discord
from discord.ext import commands
import asyncio
import socket
import struct
from datetime import datetime
import re
import json
import time
from rcon import Client
import logging
import os

# Cambiar estas líneas:
TOKEN = os.getenv('DISCORD_TOKEN')
RCON_PASSWORD = os.getenv('RCON_PASSWORD')

# Configurar logging para debug
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Comandos específicos de IOSoccer optimizados
IOSOCCER_COMMANDS = [
    # Comandos básicos esenciales
    'status',
    'version', 
    'echo "test"',
    
    # Comandos específicos de IOSoccer - MÁS PROBABLE QUE FUNCIONEN
    'sv_matchinfojson',    # Info en JSON - PRIORIDAD
    'ios_match_info',      # Información del partido
    'ios_score',           # Marcador
    'ios_time',            # Tiempo del partido
    'ios_players',         # Lista de jugadores
    
    # Comandos alternativos comunes
    'users',
    'listplayers',
    'players',
    'stats',
]

# Configuración de servidores
SERVERS = [
    {
        'name': 'Servidor IOSoccer #1',
        'ip': '45.235.98.16',
        'port': 27018,
        'rcon_ports': [27018, 27015, 27019, 27020, 27021],
    },
    {
        'name': 'Servidor IOSoccer #2', 
        'ip': '45.235.98.16',
        'port': 27019,
        'rcon_ports': [27019, 27016, 27020, 27021, 27022],
    }
]

# Bot configuration
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

class ServerInfo:
    """Clase para almacenar información del servidor"""
    def __init__(self, name, status, players=0, max_players=0, map_name="N/A", 
                 match_info=None, basic_info=None):
        self.name = name
        self.status = status
        self.players = players
        self.max_players = max_players
        self.map_name = map_name
        self.match_info = match_info  # JSON data del partido
        self.basic_info = basic_info  # Info básica A2S

class A2SQuery:
    """Clase para consultas A2S_INFO simplificada"""
    
    @staticmethod
    def query_server(ip, port, timeout=5):
        """Consulta información básica del servidor usando A2S_INFO"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(timeout)
            
            # Packet A2S_INFO
            packet = b'\xFF\xFF\xFF\xFF\x54Source Engine Query\x00'
            sock.sendto(packet, (ip, port))
            data, addr = sock.recvfrom(4096)
            sock.close()
            
            if len(data) < 25:
                return None
                
            offset = 6  # Skip header and protocol
            
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
            
            # Skip ID (2 bytes)
            offset += 2
            
            # Players and max_players
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
    """Manejador RCON usando rcon-client - MUCHO MÁS SIMPLE"""
    
    @staticmethod
    async def test_rcon_connection(ip, port, password, timeout=10):
        """
        Prueba la conexión RCON de forma simple
        Returns: {'success': bool, 'error': str, 'response': str}
        """
        try:
            logger.info(f"🔌 Probando RCON {ip}:{port}")
            
            # Usar rcon-client - MUY SIMPLE
            with Client(ip, port, passwd=password, timeout=timeout) as client:
                # Comando de prueba simple
                response = client.run('echo "RCON_TEST_OK"')
                
                if response and 'RCON_TEST_OK' in response:
                    logger.info(f"✅ RCON {ip}:{port} - Conexión exitosa")
                    return {
                        'success': True,
                        'error': None,
                        'response': response.strip()
                    }
                else:
                    logger.warning(f"🔶 RCON {ip}:{port} - Respuesta inesperada: {response}")
                    return {
                        'success': True,  # Conecta pero respuesta rara
                        'error': 'Respuesta inesperada',
                        'response': response
                    }
                    
        except Exception as e:
            logger.error(f"❌ RCON {ip}:{port} - Error: {e}")
            return {
                'success': False,
                'error': str(e),
                'response': None
            }
    
    @staticmethod
    async def execute_command(ip, port, password, command, timeout=10):
        """
        Ejecuta un comando RCON de forma simple
        Returns: {'success': bool, 'response': str, 'error': str}
        """
        try:
            logger.info(f"🔄 Ejecutando: {command} en {ip}:{port}")
            
            with Client(ip, port, passwd=password, timeout=timeout) as client:
                response = client.run(command)
                
                if response and len(response.strip()) > 0:
                    logger.info(f"✅ Comando exitoso: {len(response)} caracteres")
                    return {
                        'success': True,
                        'response': response.strip(),
                        'error': None
                    }
                else:
                    logger.warning(f"⚠️ Sin respuesta para: {command}")
                    return {
                        'success': False,
                        'response': '',
                        'error': 'Sin respuesta'
                    }
                    
        except Exception as e:
            logger.error(f"❌ Error comando '{command}': {e}")
            return {
                'success': False,
                'response': '',
                'error': str(e)
            }
    
    @staticmethod
    async def find_working_rcon_port(server, password):
        """
        Encuentra el puerto RCON que funciona
        Returns: {'port': int, 'success': bool, 'error': str}
        """
        logger.info(f"🔍 Buscando puerto RCON funcional para {server['name']}")
        
        for port in server['rcon_ports']:
            test_result = await RCONManager.test_rcon_connection(
                server['ip'], port, password, timeout=8
            )
            
            if test_result['success']:
                logger.info(f"✅ Puerto RCON funcional encontrado: {port}")
                return {
                    'port': port,
                    'success': True,
                    'error': None
                }
        
        logger.error(f"❌ No se encontró puerto RCON funcional para {server['name']}")
        return {
            'port': None,
            'success': False,
            'error': 'No hay puertos RCON funcionales'
        }
    
    @staticmethod
    async def get_match_info_json(server, password):
        """
        Obtiene información del partido usando sv_matchinfojson
        Returns: {'success': bool, 'data': dict, 'working_port': int, 'error': str}
        """
        # Encontrar puerto funcional
        port_result = await RCONManager.find_working_rcon_port(server, password)
        
        if not port_result['success']:
            return {
                'success': False,
                'data': None,
                'working_port': None,
                'error': port_result['error']
            }
        
        working_port = port_result['port']
        logger.info(f"🎮 Obteniendo match info JSON desde puerto {working_port}")
        
        # Ejecutar sv_matchinfojson
        result = await RCONManager.execute_command(
            server['ip'], working_port, password, 'sv_matchinfojson', timeout=10
        )
        
        if not result['success'] or not result['response']:
            return {
                'success': False,
                'data': None,
                'working_port': working_port,
                'error': result['error'] or 'Sin respuesta JSON'
            }
        
        # Parsear JSON
        try:
            # Limpiar la respuesta (puede tener texto extra)
            response = result['response'].strip()
            
            # Buscar el JSON en la respuesta
            json_start = response.find('{')
            json_end = response.rfind('}')
            
            if json_start == -1 or json_end == -1:
                return {
                    'success': False,
                    'data': None,
                    'working_port': working_port,
                    'error': 'No se encontró JSON válido en la respuesta'
                }
            
            json_text = response[json_start:json_end+1]
            match_data = json.loads(json_text)
            
            logger.info(f"✅ JSON parseado exitosamente: {len(json_text)} caracteres")
            
            return {
                'success': True,
                'data': match_data,
                'working_port': working_port,
                'error': None
            }
            
        except json.JSONDecodeError as e:
            logger.error(f"❌ Error parsing JSON: {e}")
            return {
                'success': False,
                'data': None,
                'working_port': working_port,
                'error': f'Error JSON: {str(e)}'
            }

def parse_match_info(match_data):
    """
    Parsea la información del partido desde el JSON
    Returns: dict con información organizada
    """
    if not match_data:
        return None
    
    try:
        # Información básica del partido
        info = {
            'period': match_data.get('matchPeriod', 'N/A'),
            'time_display': match_data.get('matchDisplaySeconds', '0:00'),
            'time_seconds': match_data.get('matchSeconds', 0),
            'map_name': match_data.get('mapName', 'N/A'),
            'format': f"{match_data.get('matchFormat', 0)}v{match_data.get('matchFormat', 0)}",
            'players_count': match_data.get('serverPlayerCount', 0),
            'max_players': match_data.get('serverMaxPlayers', 0),
            
            # Equipos y marcador
            'team_home': match_data.get('teamNameHome', 'Local'),
            'team_away': match_data.get('teamNameAway', 'Visitante'),
            'code_home': match_data.get('teamCodeHome', 'LOC'),
            'code_away': match_data.get('teamCodeAway', 'VIS'),
            'goals_home': match_data.get('matchGoalsHome', 0),
            'goals_away': match_data.get('matchGoalsAway', 0),
            
            # Lineup info
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
        # Embed simple sin información de partido
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
    
    # Embed completo con información del partido
    match_info = server_info.match_info
    
    # Color según el estado del partido
    if match_info['period'] in ['FIRST HALF', 'SECOND HALF']:
        color = 0x00ff00  # Verde - En juego
    elif match_info['period'] == 'HALF TIME':
        color = 0xffa500  # Naranja - Descanso
    else:
        color = 0x0099ff  # Azul - Otro estado
    
    embed = discord.Embed(
        title=f"⚽ {server_info.name} - {match_info['format']}",
        color=color,
        timestamp=datetime.now()
    )
    
    # Marcador principal
    score_text = f"**{match_info['team_home']} {match_info['goals_home']} - {match_info['goals_away']} {match_info['team_away']}**"
    
    embed.add_field(
        name="🏆 Marcador",
        value=f"{score_text}\n"
              f"⏱️ **{match_info['time_display']}** | 📅 **{match_info['period']}**",
        inline=False
    )
    
    # Información del servidor
    server_config = next((s for s in SERVERS if s['name'] == server_info.name), None)
    connect_info = f"{server_config['ip']}:{server_config['port']}" if server_config else "N/A"
    
    embed.add_field(
        name="🎮 Información del Servidor",
        value=f"**👥 Jugadores:** {match_info['players_count']}/{match_info['max_players']}\n"
              f"**🗺️ Mapa:** {match_info['map_name']}\n"
              f"**🌐 Conectar:** `connect {connect_info};password elo`",
        inline=False
    )
    
    # Equipos y jugadores activos
    home_players = get_active_players(match_info['lineup_home'])
    away_players = get_active_players(match_info['lineup_away'])
    
    if home_players or away_players:
        # Equipo Local
        if home_players:
            home_text = ""
            for player in home_players[:6]:  # Máximo 6 para no saturar
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
        
        # Equipo Visitante
        if away_players:
            away_text = ""
            for player in away_players[:6]:  # Máximo 6 para no saturar
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
    
    # Eventos del partido (si hay)
    if match_info['events'] and len(match_info['events']) > 0:
        events_text = ""
        for event in match_info['events'][-3:]:  # Últimos 3 eventos
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
            
            # Verificar si hay partido activo
            if (server_info.match_info and 
                server_info.match_info['period'] in ['FIRST HALF', 'SECOND HALF']):
                active_matches += 1
    
    # Resumen general
    summary = f"**🌐 Servidores Online:** {online_count}/{len(servers_info)}\n"
    summary += f"**👥 Jugadores Totales:** {total_players}\n"
    summary += f"**⚽ Partidos Activos:** {active_matches}"
    
    embed.add_field(
        name="📊 Resumen General",
        value=summary,
        inline=False
    )
    
    embed.set_footer(
        text=f"🔄 Actualizado con Match Info JSON | {datetime.now().strftime('%H:%M:%S')}"
    )
    
    return embed

async def get_server_info(server):
    """Obtiene información completa del servidor"""
    try:
        logger.info(f"📡 Consultando servidor: {server['name']}")
        
        # 1. Información básica con A2S_INFO
        a2s_info = A2SQuery.query_server(server['ip'], server['port'], timeout=6)
        
        if not a2s_info:
            return ServerInfo(
                name=server['name'],
                status="🔴 Offline"
            )
        
        # 2. Información del partido con sv_matchinfojson
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
        return ServerInfo(
            name=server['name'],
            status="🔴 Error",
        )

# ============= COMANDOS DEL BOT =============

@bot.event
async def on_ready():
    """Bot listo"""
    logger.info(f'🤖 {bot.user.name} conectado!')
    logger.info(f'🔧 Usando rcon-client con Match Info JSON')
    logger.info(f'🎮 Monitoreando {len(SERVERS)} servidores IOSoccer')
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
        
        # Limpiar field para próxima iteración
        loading_embed.clear_fields()
    
    # Mostrar resumen general primero
    status_embed = create_status_embed(servers_info)
    await message.edit(embed=status_embed)
    
    # Luego mostrar cada servidor individualmente con detalles
    for server_info in servers_info:
        if "Online" in server_info.status:
            match_embed = create_match_embed(server_info)
            await ctx.send(embed=match_embed)
        else:
            # Para servidores offline, mostrar embed simple
            offline_embed = create_match_embed(server_info)
            await ctx.send(embed=offline_embed)

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
    """Prueba comando RCON específico - SUPER SIMPLE"""
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
    
    # Encontrar puerto funcional
    port_result = await RCONManager.find_working_rcon_port(server, RCON_PASSWORD)
    
    if not port_result['success']:
        embed.add_field(
            name="❌ Error",
            value=f"No se pudo conectar RCON: {port_result['error']}",
            inline=False
        )
        await ctx.send(embed=embed)
        return
    
    # Ejecutar comando
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
        
        if len(response) > 1500:
            embed.add_field(
                name="📊 Info",
                value=f"Respuesta completa: {len(response)} caracteres",
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
    
    # Obtener JSON del partido
    match_result = await RCONManager.get_match_info_json(server, RCON_PASSWORD)
    
    if not match_result['success']:
        error_embed = discord.Embed(
            title="❌ Error obteniendo Match JSON",
            description=f"**Error:** {match_result['error']}",
            color=0xff0000
        )
        await message.edit(embed=error_embed)
        return
    
    # Mostrar JSON formateado
    json_text = json.dumps(match_result['data'], indent=2, ensure_ascii=False)
    
    if len(json_text) > 1900:  # Límite de Discord
        # Dividir en partes
        json_preview = json_text[:1900] + "\n... (truncado)"
        
        embed = discord.Embed(
            title=f"📋 Match JSON - {server['name']}",
            description=f"Puerto RCON: {match_result['working_port']}",
            color=0x00ff00
        )
        
        embed.add_field(
            name="📄 JSON Data",
            value=f"```json\n{json_text}\n```",
            inline=False
        )
    
    await message.edit(embed=embed)

@bot.command(name='test_all_commands')
async def test_all_commands(ctx, server_num: int = 1):
    """Prueba TODOS los comandos IOSoccer"""
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ Solo administradores")
        return
    
    if server_num < 1 or server_num > len(SERVERS):
        await ctx.send(f"❌ Servidor inválido. Usa 1-{len(SERVERS)}")
        return
    
    server = SERVERS[server_num - 1]
    
    embed = discord.Embed(
        title=f"🧪 Test Todos los Comandos - {server['name']}",
        description="Probando todos los comandos IOSoccer...",
        color=0xff6600
    )
    
    message = await ctx.send(embed=embed)
    
    # Encontrar puerto funcional
    port_result = await RCONManager.find_working_rcon_port(server, RCON_PASSWORD)
    
    if not port_result['success']:
        embed.description = f"❌ Error: {port_result['error']}"
        await message.edit(embed=embed)
        return
    
    working_port = port_result['port']
    embed.description = f"Puerto RCON funcional: {working_port}"
    await message.edit(embed=embed)
    
    # Probar todos los comandos
    successful_commands = []
    failed_commands = []
    
    for i, command in enumerate(IOSOCCER_COMMANDS):
        embed.description = f"Puerto: {working_port} | Probando: {command} ({i+1}/{len(IOSOCCER_COMMANDS)})"
        await message.edit(embed=embed)
        
        result = await RCONManager.execute_command(
            server['ip'], working_port, RCON_PASSWORD, command, timeout=8
        )
        
        if result['success'] and result['response']:
            successful_commands.append({
                'command': command,
                'response': result['response'],
                'length': len(result['response'])
            })
        else:
            failed_commands.append({
                'command': command,
                'error': result['error']
            })
    
    # Mostrar resultados
    embed.description = f"✅ Completado - Puerto RCON: {working_port}"
    
    if successful_commands:
        success_text = ""
        for cmd_info in successful_commands[:8]:  # Máximo 8 para no exceder límites
            success_text += f"✅ **{cmd_info['command']}**: {cmd_info['length']} chars\n"
        
        if len(successful_commands) > 8:
            success_text += f"... y {len(successful_commands) - 8} más"
        
        embed.add_field(
            name=f"✅ Comandos Exitosos ({len(successful_commands)})",
            value=success_text or "Ninguno",
            inline=False
        )
    
    if failed_commands:
        failed_text = ""
        for cmd_info in failed_commands[:8]:
            failed_text += f"❌ **{cmd_info['command']}**: {cmd_info['error'][:30]}...\n"
        
        if len(failed_commands) > 8:
            failed_text += f"... y {len(failed_commands) - 8} más"
        
        embed.add_field(
            name=f"❌ Comandos Fallidos ({len(failed_commands)})",
            value=failed_text or "Ninguno",
            inline=False
        )
    
    # Mostrar el comando más prometedor
    if successful_commands:
        best_command = max(successful_commands, key=lambda x: x['length'])
        preview = best_command['response'][:200]
        if len(best_command['response']) > 200:
            preview += "..."
        
        embed.add_field(
            name=f"🎯 Mejor Comando: {best_command['command']}",
            value=f"```\n{preview}\n```",
            inline=False
        )
    
    await message.edit(embed=embed)

@bot.command(name='fix_guide')
async def rcon_fix_guide(ctx):
    """Guía para configurar RCON correctamente"""
    embed = discord.Embed(
        title="🛠️ Guía: Configurar RCON IOSoccer",
        description="Configuración paso a paso para RCON con Match Info JSON",
        color=0xff6600
    )
    
    # Instalación rcon-client
    install_guide = """```bash
# 1. Instalar rcon-client (Python)
pip install rcon

# 2. Test manual desde terminal:
python -c "
from rcon import Client
with Client('45.235.98.16', 27018, passwd='tu_password') as client:
    print(client.run('sv_matchinfojson'))
"
```"""
    
    embed.add_field(
        name="📥 1. Instalación y Test",
        value=install_guide,
        inline=False
    )
    
    # Configuración server.cfg
    server_cfg = """```cfg
// Configuración RCON básica
rcon_password "tu_password_aqui"
sv_rcon_banpenalty 0
sv_rcon_maxfailures 10

// Network
sv_lan 0
hostport 27018

// IOSoccer específico (si aplica)
sv_match_info_enabled 1
```"""
    
    embed.add_field(
        name="📝 2. server.cfg",
        value=server_cfg,
        inline=False
    )
    
    # Comandos importantes
    commands_guide = """```
sv_matchinfojson    - Info completa del partido en JSON
status              - Estado del servidor
users               - Lista de usuarios conectados
listplayers         - Lista de jugadores
```"""
    
    embed.add_field(
        name="🎮 3. Comandos Clave",
        value=commands_guide,
        inline=False
    )
    
    # Verificación
    verification = """```bash
# Verificar que el servidor IOSoccer esté corriendo:
ps aux | grep srcds

# Verificar puertos abiertos:
netstat -tulpn | grep :27018

# Test específico del comando JSON:
!rcon 1 sv_matchinfojson
```"""
    
    embed.add_field(
        name="🔍 4. Verificación",
        value=verification,
        inline=False
    )
    
    embed.set_footer(text="💡 Usa !status para ver información organizada del partido")
    
    await ctx.send(embed=embed)

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

@bot.command(name='help')
async def help_command(ctx):
    """Ayuda del bot"""
    embed = discord.Embed(
        title="🤖 Bot IOSoccer con Match Info JSON",
        description="Bot mejorado con información detallada de partidos",
        color=0x0099ff
    )
    
    commands_help = [
        ("🎮 !status", "Estado completo de todos los servidores con info de partidos"),
        ("⚽ !server [1-2]", "Información detallada de un servidor específico"),
        ("📋 !matchjson [1-2]", "JSON completo del partido en curso"),
        ("🔧 !rcon [1-2] [comando]", "(Admin) Ejecuta comando RCON específico"),
        ("🧪 !test_all_commands [1-2]", "(Admin) Prueba todos los comandos IOSoccer"),
        ("🛠️ !fix_guide", "Guía para configurar RCON correctamente"),
        ("🏓 !ping", "Latencia del bot"),
    ]
    
    for name, description in commands_help:
        embed.add_field(name=name, value=description, inline=False)
    
    embed.add_field(
        name="📊 Información Mostrada",
        value="• Marcador en tiempo real\n• Tiempo de juego\n• Equipos y jugadores\n• Estado del partido\n• Información de conexión",
        inline=False
    )
    
    embed.set_footer(text="🔧 Versión mejorada con sv_matchinfojson")
    
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

# ============= EJECUTAR BOT =============

if __name__ == "__main__":
    print("🚀 Iniciando Bot IOSoccer con Match Info JSON")
    print("🔧 Versión mejorada con información detallada de partidos")
    print("📡 Usando sv_matchinfojson para datos en tiempo real")
    print("⚽ Mostrando marcadores, tiempos, equipos y jugadores")
    print("="*60)
    
    try:
        bot.run(TOKEN)
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        input("Presiona Enter para cerrar...")