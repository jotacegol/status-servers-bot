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
        'name': 'ELO #1',
        'ip': '45.235.98.16',
        'port': 27018,
        'rcon_ports': [27018],  # SOLO este puerto para este servidor
        'id': 'iosoccer_1',     # ID único para logs
        'max_connection_time': 30,  # Tiempo máximo total de conexión
    },
    {
        'name': 'ELO #2', 
        'ip': '45.235.98.16',
        'port': 27019,
        'rcon_ports': [27019],  # SOLO este puerto para este servidor
        'id': 'iosoccer_2',     # ID único para logs
        'max_connection_time': 30,  # Tiempo máximo total de conexión
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
    """Manejador RCON mejorado con reintentos y timeouts progresivos"""
    
    @staticmethod
    async def test_rcon_connection_robust(ip, port, password, max_retries=3):
        """
        Prueba la conexión RCON con reintentos automáticos
        Returns: {'success': bool, 'error': str, 'response': str, 'attempts': int}
        """
        last_error = None
        
        # Timeouts progresivos: 8s, 12s, 15s
        timeouts = [8, 12, 15]
        
        for attempt in range(max_retries):
            timeout = timeouts[min(attempt, len(timeouts) - 1)]
            
            try:
                logger.info(f"🔌 Intento {attempt + 1}/{max_retries} RCON {ip}:{port} (timeout: {timeout}s)")
                
                # Usar timeout progresivo
                with Client(ip, port, passwd=password, timeout=timeout) as client:
                    # Comando de prueba simple pero confiable
                    response = client.run('echo "RCON_TEST_OK"')
                    
                    if response and 'RCON_TEST_OK' in response:
                        logger.info(f"✅ RCON {ip}:{port} - Conectado en intento {attempt + 1}")
                        return {
                            'success': True,
                            'error': None,
                            'response': response.strip(),
                            'attempts': attempt + 1
                        }
                    else:
                        last_error = f"Respuesta inesperada: {response}"
                        logger.warning(f"🔶 RCON {ip}:{port} - {last_error}")
                        
            except Exception as e:
                last_error = str(e)
                logger.warning(f"⚠️ RCON {ip}:{port} intento {attempt + 1} falló: {e}")
                
                # Esperar antes del siguiente intento (excepto en el último)
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)
        
        logger.error(f"❌ RCON {ip}:{port} - Falló después de {max_retries} intentos")
        return {
            'success': False,
            'error': f"Falló después de {max_retries} intentos: {last_error}",
            'response': None,
            'attempts': max_retries
        }
    
    @staticmethod
    async def execute_command_robust(ip, port, password, command, max_retries=2):
        """
        Ejecuta un comando RCON con reintentos
        Returns: {'success': bool, 'response': str, 'error': str, 'attempts': int}
        """
        last_error = None
        
        for attempt in range(max_retries):
            try:
                # Timeout más largo para comandos complejos como sv_matchinfojson
                timeout = 15 if 'matchinfo' in command.lower() else 10
                
                logger.info(f"🔄 Ejecutando '{command}' en {ip}:{port} (intento {attempt + 1}, timeout: {timeout}s)")
                
                with Client(ip, port, passwd=password, timeout=timeout) as client:
                    response = client.run(command)
                    
                    if response is not None and len(response.strip()) > 0:
                        logger.info(f"✅ Comando '{command}' exitoso en intento {attempt + 1}: {len(response)} chars")
                        return {
                            'success': True,
                            'response': response.strip(),
                            'error': None,
                            'attempts': attempt + 1
                        }
                    else:
                        last_error = 'Sin respuesta del servidor'
                        logger.warning(f"⚠️ '{command}' sin respuesta en intento {attempt + 1}")
                        
            except Exception as e:
                last_error = str(e)
                logger.warning(f"⚠️ '{command}' falló intento {attempt + 1}: {e}")
                
                # Esperar antes del siguiente intento
                if attempt < max_retries - 1:
                    await asyncio.sleep(1.5)
        
        logger.error(f"❌ Comando '{command}' falló después de {max_retries} intentos")
        return {
            'success': False,
            'response': '',
            'error': f"Falló después de {max_retries} intentos: {last_error}",
            'attempts': max_retries
        }
    
    @staticmethod
    async def find_working_rcon_port_safe(server, password):
        """
        Encuentra el puerto RCON funcional de forma SEGURA
        Solo prueba los puertos específicos definidos para cada servidor
        Returns: {'port': int, 'success': bool, 'error': str, 'attempts_per_port': dict}
        """
        logger.info(f"🔍 Buscando puerto RCON seguro para {server['name']}")
        
        # VALIDACIÓN: Solo usar puertos explícitamente definidos
        allowed_ports = server.get('rcon_ports', [])
        if not allowed_ports:
            return {
                'port': None,
                'success': False,
                'error': 'No hay puertos RCON definidos para este servidor',
                'attempts_per_port': {}
            }
        
        logger.info(f"🛡️ Puertos permitidos para {server['name']}: {allowed_ports}")
        
        attempts_log = {}
        
        # Probar SOLO los puertos permitidos
        for port in allowed_ports:
            logger.info(f"🔐 Probando puerto seguro: {port}")
            
            test_result = await RCONManager.test_rcon_connection_robust(
                server['ip'], port, password, max_retries=3
            )
            
            attempts_log[port] = {
                'attempts': test_result.get('attempts', 0),
                'success': test_result['success'],
                'error': test_result.get('error', '')
            }
            
            if test_result['success']:
                logger.info(f"✅ Puerto RCON seguro encontrado: {port} (después de {test_result['attempts']} intentos)")
                return {
                    'port': port,
                    'success': True,
                    'error': None,
                    'attempts_per_port': attempts_log
                }
            else:
                logger.warning(f"❌ Puerto {port} falló: {test_result['error']}")
        
        # Si ningún puerto funciona
        error_summary = f"Ningún puerto RCON funcional encontrado. Intentos: "
        for port, info in attempts_log.items():
            error_summary += f"{port}({info['attempts']}x), "
        error_summary = error_summary.rstrip(', ')
        
        logger.error(f"❌ {error_summary}")
        return {
            'port': None,
            'success': False,
            'error': error_summary,
            'attempts_per_port': attempts_log
        }
    
    @staticmethod
    async def get_match_info_json_safe(server, password):
        """
        Obtiene información del partido de forma SEGURA con reintentos
        Returns: {'success': bool, 'data': dict, 'working_port': int, 'error': str, 'connection_info': dict}
        """
        logger.info(f"🎮 Obteniendo match info JSON SEGURO para {server['name']}")
        
        # 1. Encontrar puerto funcional de forma segura
        port_result = await RCONManager.find_working_rcon_port_safe(server, password)
        
        if not port_result['success']:
            return {
                'success': False,
                'data': None,
                'working_port': None,
                'error': f"Sin puertos RCON: {port_result['error']}",
                'connection_info': port_result['attempts_per_port']
            }
        
        working_port = port_result['port']
        logger.info(f"🔐 Usando puerto seguro {working_port} para match info")
        
        # 2. Ejecutar sv_matchinfojson con reintentos
        result = await RCONManager.execute_command_robust(
            server['ip'], working_port, password, 'sv_matchinfojson', max_retries=3
        )
        
        if not result['success'] or not result['response']:
            return {
                'success': False,
                'data': None,
                'working_port': working_port,
                'error': f"Fallo comando JSON: {result['error']}",
                'connection_info': {
                    'port_attempts': port_result['attempts_per_port'],
                    'command_attempts': result.get('attempts', 0)
                }
            }
        
        # 3. Parsear JSON con manejo de errores mejorado
        try:
            response = result['response'].strip()
            logger.info(f"📄 Respuesta cruda recibida: {len(response)} caracteres")
            
            # Buscar JSON en la respuesta de forma más robusta
            json_start = response.find('{')
            json_end = response.rfind('}')
            
            if json_start == -1 or json_end == -1 or json_start >= json_end:
                # Intentar buscar patrones alternativos
                lines = response.split('\n')
                json_line = None
                for line in lines:
                    line = line.strip()
                    if line.startswith('{') and line.endswith('}'):
                        json_line = line
                        break
                
                if not json_line:
                    return {
                        'success': False,
                        'data': None,
                        'working_port': working_port,
                        'error': f'JSON no encontrado en respuesta de {len(response)} caracteres',
                        'connection_info': {
                            'raw_response_preview': response[:200] + '...' if len(response) > 200 else response
                        }
                    }
                
                json_text = json_line
            else:
                json_text = response[json_start:json_end+1]
            
            # Parsear JSON
            match_data = json.loads(json_text)
            
            logger.info(f"✅ JSON parseado exitosamente: {len(json_text)} caracteres, {len(match_data)} campos")
            
            return {
                'success': True,
                'data': match_data,
                'working_port': working_port,
                'error': None,
                'connection_info': {
                    'port_attempts': port_result['attempts_per_port'],
                    'command_attempts': result.get('attempts', 0),
                    'json_size': len(json_text)
                }
            }
            
        except json.JSONDecodeError as e:
            logger.error(f"❌ Error parsing JSON: {e}")
            return {
                'success': False,
                'data': None,
                'working_port': working_port,
                'error': f'JSON inválido: {str(e)}',
                'connection_info': {
                    'json_text_preview': json_text[:300] if 'json_text' in locals() else 'N/A',
                    'parse_error': str(e)
                }
            }

def parse_match_info(match_data):
    """
    Parsea la información del partido desde el JSON COMPLETO
    Returns: dict con información organizada incluyendo goles detallados
    """
    if not match_data:
        return None
    
    try:
        # Obtener información básica del partido
        match_info = match_data.get('matchData', {}).get('matchInfo', {})
        teams = match_data.get('matchData', {}).get('teams', [])
        players = match_data.get('matchData', {}).get('players', [])
        events = match_data.get('matchData', {}).get('matchEvents', [])
        
        # Calcular tiempo actual del partido en formato MM:SS
        start_time = match_info.get('startTime', 0)
        end_time = match_info.get('endTime', 0)
        total_seconds = end_time - start_time
        
        # Convertir a formato MM:SS
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        time_display = f"{minutes}:{seconds:02d}"
        
        # Información básica
        info = {
            'period': match_info.get('lastPeriodName', 'N/A'),
            'time_display': time_display,  # Tiempo formateado
            'time_seconds': total_seconds,
            'map_name': match_info.get('mapName', 'N/A'),
            'format': f"{match_info.get('format', 8)}v{match_info.get('format', 8)}",
            'match_type': match_info.get('type', 'N/A'),
            'server_name': match_info.get('serverName', 'N/A'),
            
            # Información de equipos
            'team_home': teams[0].get('matchTotal', {}).get('name', 'Local') if len(teams) > 0 else 'Local',
            'team_away': teams[1].get('matchTotal', {}).get('name', 'Visitante') if len(teams) > 1 else 'Visitante',
            'goals_home': teams[0].get('matchTotal', {}).get('statistics', [0]*28)[12] if len(teams) > 0 else 0,  # índice 12 = goles
            'goals_away': teams[1].get('matchTotal', {}).get('statistics', [0]*28)[12] if len(teams) > 1 else 0,
            
            # Contar jugadores (excluyendo bots)
            'players_count': count_real_players(players),
            'max_players': match_info.get('format', 8) * 2,  # Estimado basado en formato
            
            # Información de jugadores y eventos
            'players': players,
            'events': events,
            'goals_detail': parse_goals_from_events(events, players),
            
            # Para compatibilidad con la función original
            'lineup_home': extract_team_players(players, 'home'),
            'lineup_away': extract_team_players(players, 'away'),
            
            'match_stats': {
                'home_stats': teams[0].get('matchTotal', {}).get('statistics', []) if len(teams) > 0 else [],
                'away_stats': teams[1].get('matchTotal', {}).get('statistics', []) if len(teams) > 1 else []
            }
        }
        
        return info
        
    except Exception as e:
        logger.error(f"❌ Error parsing match info completo: {e}")
        return None

def count_real_players(players):
    """
    Cuenta solo los jugadores reales (no bots)
    """
    real_players = 0
    for player in players:
        steam_id = player.get('info', {}).get('steamId', '')
        if steam_id and steam_id != 'BOT' and steam_id != 'SourceTV':
            real_players += 1
    return real_players

def extract_team_players(players, team_side):
    """
    Extrae jugadores de un equipo específico para compatibilidad
    """
    team_players = []
    
    for player in players:
        player_info = player.get('info', {})
        steam_id = player_info.get('steamId', '')
        name = player_info.get('name', 'Unknown')
        
        if steam_id == 'BOT' or steam_id == 'SourceTV' or not steam_id:
            continue
            
        # Buscar la posición más reciente del jugador
        periods = player.get('matchPeriodData', [])
        if not periods:
            continue
            
        # Obtener el último período jugado
        last_period = periods[-1]
        last_team = last_period.get('info', {}).get('team', '')
        last_position = last_period.get('info', {}).get('position', 'N/A')
        
        if last_team == team_side:
            team_players.append({
                'steamId': steam_id,
                'name': name,
                'position': last_position
            })
    
    return team_players

def parse_goals_from_events(events, players):
    """
    Extrae información detallada de goles desde los eventos
    Returns: list de dict con información de cada gol
    """
    goals = []
    
    # Crear diccionario de jugadores para búsqueda rápida
    player_dict = {}
    for player in players:
        steam_id = player.get('info', {}).get('steamId', '')
        name = player.get('info', {}).get('name', 'Unknown')
        if steam_id and steam_id != 'BOT' and steam_id != 'SourceTV':
            player_dict[steam_id] = name
    
    # Procesar eventos de goles
    for event in events:
        if event.get('event') == 'GOAL':
            scorer_id = event.get('player1SteamId', '')
            assist_id = event.get('player2SteamId', '')
            
            goal_info = {
                'minute': seconds_to_minutes(event.get('second', 0)),
                'period': event.get('period', 'N/A'),
                'team': event.get('team', 'N/A'),
                'scorer_id': scorer_id,
                'assist_id': assist_id,
                'scorer_name': player_dict.get(scorer_id, 'Unknown'),
                'assist_name': player_dict.get(assist_id, '') if assist_id else '',
                'body_part': event.get('bodyPart', 1),  # 1=pie, 4=cabeza
                'position': event.get('startPosition', {})
            }
            goals.append(goal_info)
    
    return goals

def seconds_to_minutes(seconds):
    """
    Convierte segundos a formato MM:SS
    """
    if not seconds:
        return "0:00"
    
    minutes = seconds // 60
    remaining_seconds = seconds % 60
    return f"{minutes}:{remaining_seconds:02d}"

def get_player_goals_stats(players, team_side):
    """
    Obtiene estadísticas de goles por jugador de un equipo específico
    """
    player_goals = {}
    
    for player in players:
        player_info = player.get('info', {})
        steam_id = player_info.get('steamId', '')
        name = player_info.get('name', 'Unknown')
        
        if steam_id == 'BOT' or not steam_id:
            continue
        
        total_goals = 0
        total_assists = 0
        
        # Sumar goles de todos los períodos
        for period_data in player.get('matchPeriodData', []):
            period_info = period_data.get('info', {})
            if period_info.get('team') == team_side:
                stats = period_data.get('statistics', [])
                if len(stats) > 12:  # índice 12 = goles
                    total_goals += stats[12]
                if len(stats) > 14:  # índice 14 = asistencias
                    total_assists += stats[14]
        
        if total_goals > 0 or total_assists > 0:
            player_goals[steam_id] = {
                'name': name,
                'goals': total_goals,
                'assists': total_assists
            }
    
    return player_goals

def format_goals_display(goals_detail, team_side):
    """
    Formatea la información de goles para mostrar en el embed
    """
    team_goals = [goal for goal in goals_detail if goal['team'] == team_side]
    
    if not team_goals:
        return "Sin goles"
    
    goals_text = ""
    for goal in team_goals:
        scorer = goal['scorer_name']
        minute = goal['minute']
        assist_text = f" ({goal['assist_name']})" if goal['assist_name'] else ""
        
        goals_text += f"⚽ **{minute}'** {scorer}{assist_text}\n"
    
    return goals_text.strip()

def get_top_scorers(goals_detail, limit=3):
    """
    Obtiene los máximos goleadores del partido
    """
    scorer_count = {}
    
    for goal in goals_detail:
        scorer_name = goal['scorer_name']
        if scorer_name in scorer_count:
            scorer_count[scorer_name] += 1
        else:
            scorer_count[scorer_name] = 1
    
    # Ordenar por cantidad de goles
    top_scorers = sorted(scorer_count.items(), key=lambda x: x[1], reverse=True)
    
    return top_scorers[:limit]

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
                name="📊 Información del Servidor",
                value=f"**👥 Jugadores:** {server_info.players}/{server_info.max_players}\n"
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
    
    # Información del servidor
    server_config = next((s for s in SERVERS if s['name'] == server_info.name), None)
    connect_info = f"{server_config['ip']}:{server_config['port']}" if server_config else "N/A"
    
    embed.add_field(
        name="📊 Información del Servidor",
        value=f"**👥 Jugadores:** {match_info['players_count']}/{match_info['max_players']}\n"
              f"**🗺️ Mapa:** {match_info['map_name']}\n"
              f"**🌐 Conectar:** `connect {connect_info};password elo`",
        inline=False
    )
    
    # Marcador principal
    score_text = f"**{match_info['team_home']} {match_info['goals_home']} - {match_info['goals_away']} {match_info['team_away']}**"
    
    embed.add_field(
        name="🏆 Marcador",
        value=f"{score_text}\n"
              f"⏱️ **{match_info['time_display']}** | 📅 **{match_info['period']}**",
        inline=False
    )
    
    # Goles detallados por equipo
    if match_info.get('goals_detail'):
        # Formatear goles del equipo local
        home_goals = [goal for goal in match_info['goals_detail'] if goal['team'] == 'home']
        away_goals = [goal for goal in match_info['goals_detail'] if goal['team'] == 'away']
        
        # Goles equipo local
        if home_goals:
            home_goals_text = ""
            for goal in home_goals:
                assist_text = f" ({goal['assist_name']})" if goal['assist_name'] else ""
                home_goals_text += f"{goal['scorer_name']} ({goal['minute']}){assist_text}\n"
        else:
            home_goals_text = "Sin goles"
        
        embed.add_field(
            name=f"⚽ Goles {match_info['team_home']}:",
            value=home_goals_text.strip(),
            inline=True
        )
        
        # Goles equipo visitante
        if away_goals:
            away_goals_text = ""
            for goal in away_goals:
                assist_text = f" ({goal['assist_name']})" if goal['assist_name'] else ""
                away_goals_text += f"{goal['scorer_name']} ({goal['minute']}){assist_text}\n"
        else:
            away_goals_text = "Sin goles"
        
        embed.add_field(
            name=f"⚽ Goles {match_info['team_away']}:",
            value=away_goals_text.strip(),
            inline=True
        )
        
        # Campo vacío para hacer nueva línea
        embed.add_field(name="\u200b", value="\u200b", inline=True)
        
        # Máximos goleadores (si hay goles)
        if home_goals or away_goals:
            scorer_count = {}
            for goal in match_info['goals_detail']:
                scorer = goal['scorer_name']
                scorer_count[scorer] = scorer_count.get(scorer, 0) + 1
            
            # Ordenar por cantidad de goles
            top_scorers = sorted(scorer_count.items(), key=lambda x: x[1], reverse=True)
            
            if top_scorers:
                scorers_text = ""
                medals = ["🥇", "🥈", "🥉"]
                
                for i, (player_name, goal_count) in enumerate(top_scorers[:3]):
                    medal = medals[i] if i < 3 else "🏆"
                    plural = "goles" if goal_count > 1 else "gol"
                    scorers_text += f"{medal} **{player_name}** ({goal_count} {plural})\n"
                
                embed.add_field(
                    name="🏆 Máximos Goleadores",
                    value=scorers_text.strip(),
                    inline=False
                )
    else:
        # Sin información de goles detallada
        embed.add_field(
            name=f"⚽ Goles {match_info['team_home']}:",
            value="Sin información detallada",
            inline=True
        )
        
        embed.add_field(
            name=f"⚽ Goles {match_info['team_away']}:",
            value="Sin información detallada", 
            inline=True
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

# Función mejorada para obtener información del servidor
async def get_server_info_robust(server):
    """Obtiene información completa del servidor con manejo robusto de errores"""
    
    # Validar configuración del servidor
    if not server.get('rcon_ports'):
        logger.error(f"❌ Servidor {server.get('name', 'Unknown')} sin puertos RCON definidos")
        return ServerInfo(
            name=server.get('name', 'Unknown'),
            status="🔴 Error - Sin puertos RCON"
        )
    
    try:
        logger.info(f"📡 Consultando servidor robusto: {server['name']} (ID: {server.get('id', 'unknown')})")
        
        # 1. Información básica con A2S_INFO (timeout fijo)
        a2s_info = A2SQuery.query_server(server['ip'], server['port'], timeout=8)
        
        if not a2s_info:
            return ServerInfo(
                name=server['name'],
                status="🔴 Offline"
            )
        
        # 2. Información del partido con método seguro
        match_result = await RCONManager.get_match_info_json_safe(server, RCON_PASSWORD)
        
        match_info = None
        connection_details = match_result.get('connection_info', {})
        
        if match_result['success'] and match_result['data']:
            if 'matchData' in match_result['data']:
                match_info = parse_match_info(match_result['data'])
                logger.info(f"✅ Match info completa obtenida para {server['name']} (puerto {match_result['working_port']})")
            else:
                logger.info(f"📄 JSON simple para {server['name']}, usando datos básicos")
                match_info = {
                    'period': 'N/A',
                    'time_display': '0:00',
                    'time_seconds': 0,
                    'map_name': match_result['data'].get('mapName', 'N/A'),
                    'format': '8v8',
                    'players_count': 0,
                    'max_players': 16,
                    'team_home': 'Local',
                    'team_away': 'Visitante', 
                    'goals_home': 0,
                    'goals_away': 0,
                    'goals_detail': [],
                    'lineup_home': [],
                    'lineup_away': []
                }
        else:
            match_info = None
        
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
        logger.error(f"❌ Error obteniendo info robusta de {server['name']}: {e}")
        return ServerInfo(
            name=server['name'],
            status="🔴 Error General",
        )
        
def validate_server_config():
    """
    Valida que la configuración de servidores sea segura
    Returns: {'valid': bool, 'errors': list, 'warnings': list}
    """
    errors = []
    warnings = []
    
    # Verificar que no hay puertos duplicados
    all_ports = []
    for server in SERVERS:
        rcon_ports = server.get('rcon_ports', [])
        
        # Verificar configuración mínima
        if not server.get('name'):
            errors.append(f"Servidor sin nombre: {server}")
        if not server.get('ip'):
            errors.append(f"Servidor sin IP: {server.get('name', 'Unknown')}")
        if not rcon_ports:
            errors.append(f"Servidor sin puertos RCON: {server.get('name', 'Unknown')}")
        
        # Verificar puertos únicos
        for port in rcon_ports:
            if port in all_ports:
                errors.append(f"Puerto RCON duplicado {port} en {server.get('name', 'Unknown')}")
            all_ports.append(port)
        
        # Verificar que el puerto RCON coincida con el puerto del servidor (recomendado)
        if server.get('port') not in rcon_ports:
            warnings.append(f"Puerto servidor {server.get('port')} no está en puertos RCON {rcon_ports} para {server.get('name')}")
    
    logger.info(f"🔍 Configuración validada: {len(errors)} errores, {len(warnings)} advertencias")
    
    return {
        'valid': len(errors) == 0,
        'errors': errors,
        'warnings': warnings,
        'total_servers': len(SERVERS),
        'total_ports': len(all_ports)
    }
# ============= COMANDOS DEL BOT =============

@bot.event
async def on_ready():
    """Bot listo con validación completa"""
    logger.info(f'🤖 {bot.user.name} conectado!')
    
    # 1. Validar configuración
    config_validation = validate_server_config()
    
    if not config_validation['valid']:
        logger.error("❌ CONFIGURACIÓN INVÁLIDA:")
        for error in config_validation['errors']:
            logger.error(f"  - {error}")
        logger.warning("⚠️ El bot puede no funcionar correctamente")
    else:
        logger.info(f"✅ Configuración válida: {config_validation['total_servers']} servidores, {config_validation['total_ports']} puertos")
    
    if config_validation['warnings']:
        logger.warning("⚠️ ADVERTENCIAS DE CONFIGURACIÓN:")
        for warning in config_validation['warnings']:
            logger.warning(f"  - {warning}")
    
    # 2. Test inicial de conectividad RCON (no bloqueante)
    logger.info("🔍 Iniciando test de conectividad RCON...")
    
    connectivity_results = []
    for server in SERVERS:
        logger.info(f"🧪 Testing {server['name']}...")
        
        # Test rápido de cada puerto
        server_connectivity = {
            'server': server['name'],
            'ports_tested': [],
            'working_ports': [],
            'total_time': 0
        }
        
        start_time = time.time()
        
        for port in server.get('rcon_ports', []):
            port_test = await RCONManager.test_rcon_connection_robust(
                server['ip'], port, RCON_PASSWORD, max_retries=1  # Solo 1 intento en inicio
            )
            
            server_connectivity['ports_tested'].append({
                'port': port,
                'success': port_test['success'],
                'error': port_test.get('error', 'OK')
            })
            
            if port_test['success']:
                server_connectivity['working_ports'].append(port)
        
        server_connectivity['total_time'] = round(time.time() - start_time, 2)
        connectivity_results.append(server_connectivity)
        
        # Log resultado
        working_count = len(server_connectivity['working_ports'])
        total_count = len(server['rcon_ports'])
        
        if working_count > 0:
            logger.info(f"✅ {server['name']}: {working_count}/{total_count} puertos RCON funcionales en {server_connectivity['total_time']}s")
        else:
            logger.warning(f"❌ {server['name']}: 0/{total_count} puertos RCON funcionales")
    
    # 3. Resumen de conectividad
    total_working = sum(len(result['working_ports']) for result in connectivity_results)
    total_ports = sum(len(server.get('rcon_ports', [])) for server in SERVERS)
    
    logger.info("="*60)
    logger.info(f"🎮 IOSoccer Bot INICIADO")
    logger.info(f"📊 Resumen de conectividad: {total_working}/{total_ports} puertos RCON funcionales")
    logger.info(f"🔧 Usando rcon-client con Match Info JSON")
    logger.info(f"🛡️ Modo seguro: Solo puertos específicos por servidor")
    logger.info("="*60)
# Comando para diagnóstico completo
@bot.command(name='diagnose')
async def diagnose_system(ctx):
    """Diagnóstico completo del sistema RCON"""
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ Solo administradores")
        return
    
    embed = discord.Embed(
        title="🔍 Diagnóstico Completo del Sistema",
        description="Verificando configuración y conectividad RCON...",
        color=0xffaa00
    )
    
    message = await ctx.send(embed=embed)
    
    # 1. Validación de configuración
    config_validation = validate_server_config()
    
    config_status = "✅ Válida" if config_validation['valid'] else "❌ Inválida"
    config_details = f"**Estado:** {config_status}\n"
    config_details += f"**Servidores:** {config_validation['total_servers']}\n"
    config_details += f"**Puertos totales:** {config_validation['total_ports']}\n"
    
    if config_validation['errors']:
        config_details += f"**Errores:** {len(config_validation['errors'])}\n"
    if config_validation['warnings']:
        config_details += f"**Advertencias:** {len(config_validation['warnings'])}\n"
    
    embed.add_field(
        name="⚙️ Configuración",
        value=config_details,
        inline=False
    )
    
    await message.edit(embed=embed)
    
    # 2. Test de conectividad por servidor
    for i, server in enumerate(SERVERS):
        embed.description = f"Probando {server['name']} ({i+1}/{len(SERVERS)})..."
        await message.edit(embed=embed)
        
        # Test completo del servidor
        connectivity_test = await RCONManager.find_working_rcon_port_safe(server, RCON_PASSWORD)
        
        if connectivity_test['success']:
            status = f"✅ Puerto {connectivity_test['port']} funcional"
            
            # Test comando específico
            cmd_test = await RCONManager.execute_command_robust(
                server['ip'], connectivity_test['port'], RCON_PASSWORD, 'sv_matchinfojson', max_retries=1
            )
            
            if cmd_test['success']:
                status += f"\n📊 sv_matchinfojson: OK ({len(cmd_test['response'])} chars)"
            else:
                status += f"\n⚠️ sv_matchinfojson: {cmd_test['error'][:50]}..."
                
        else:
            status = f"❌ Sin puertos funcionales\n{connectivity_test['error'][:100]}..."
        
        embed.add_field(
            name=f"🎮 {server['name']}",
            value=status,
            inline=False
        )
    
    embed.description = "✅ Diagnóstico completado"
    embed.color = 0x00ff00
    
    await message.edit(embed=embed)
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
        
        server_info = await get_server_info_robust(server)
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
    
    server_info = await get_server_info_robust(server)
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
