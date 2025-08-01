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
    async def find_working_rcon_port(server, password):
        """
        FUNCIÓN FALTANTE - Alias para compatibilidad
        """
        return await RCONManager.find_working_rcon_port_safe(server, password)
    
    @staticmethod
    async def execute_command(ip, port, password, command, timeout=10):
        """
        FUNCIÓN FALTANTE - Alias para compatibilidad
        """
        return await RCONManager.execute_command_robust(ip, port, password, command, max_retries=2)
    
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
    
    @staticmethod
    async def get_match_info_json(server, password):
        """
        FUNCIÓN FALTANTE - Alias para compatibilidad
        """
        return await RCONManager.get_match_info_json_safe(server, password)

def parse_match_info(match_data):
    """
    Parsea la información del partido desde el JSON COMPLETO - VERSIÓN CORREGIDA
    Returns: dict con información organizada incluyendo goles detallados
    """
    if not match_data:
        logger.warning("⚠️ parse_match_info: match_data es None o vacío")
        return None
    
    try:
        logger.info(f"🔍 Parseando match data con {len(match_data)} campos principales")
        
        # Debug: Mostrar estructura del JSON
        if 'matchData' in match_data:
            match_core = match_data['matchData']
            logger.info(f"📊 matchData encontrado con campos: {list(match_core.keys())}")
        else:
            logger.warning("⚠️ No se encontró 'matchData' en el JSON")
            # Intentar usar el JSON directamente si no hay wrapper matchData
            match_core = match_data
        
        # Obtener información básica del partido
        match_info = match_core.get('matchInfo', {})
        teams = match_core.get('teams', [])
        players = match_core.get('players', [])
        events = match_core.get('matchEvents', [])
        
        logger.info(f"📋 Datos encontrados: matchInfo={len(match_info)}, teams={len(teams)}, players={len(players)}, events={len(events)}")
        
        # TIEMPO DEL PARTIDO CORREGIDO
        current_time_seconds = 0
        period_name = "N/A"
        
        # NUEVO: Obtener el tiempo más reciente desde los eventos o matchInfo
        if events:
            # Buscar el evento más reciente para obtener el tiempo actual
            latest_event = max(events, key=lambda x: x.get('second', 0))
            current_time_seconds = latest_event.get('second', 0)
            logger.info(f"⏰ Tiempo desde último evento: {current_time_seconds}s")
        
        # Obtener período desde matchInfo
        if 'lastPeriodName' in match_info:
            period_name = match_info['lastPeriodName']
        elif 'currentPeriod' in match_info:
            period_name = match_info['currentPeriod']
        elif 'period' in match_info:
            period_name = match_info['period']
        
        # Si el partido terminó, usar endTime si está disponible
        if match_info.get('endTime') and period_name in ['FULL TIME', 'FINISHED']:
            # Calcular duración total del partido
            start_time = match_info.get('startTime', 0)
            end_time = match_info.get('endTime', 0)
            if start_time and end_time:
                total_duration = end_time - start_time
                current_time_seconds = min(current_time_seconds, total_duration)
        
        # Convertir segundos a formato MM:SS
        if current_time_seconds > 0:
            minutes = int(current_time_seconds // 60)
            seconds = int(current_time_seconds % 60)
            time_display = f"{minutes}:{seconds:02d}"
        else:
            time_display = "0:00"
        
        logger.info(f"⏰ Tiempo parseado: {time_display} ({current_time_seconds}s) en período '{period_name}'")
        
        # ========== CORRECCIÓN PRINCIPAL: NOMBRES DE EQUIPOS ==========
        team_home_name = "Local"
        team_away_name = "Visitante"
        goals_home = 0
        goals_away = 0
        
        if len(teams) >= 2:
            # NUEVO: Extraer nombres de equipos desde matchTotal
            home_team_data = teams[0]
            away_team_data = teams[1]
            
            # Los nombres están en matchTotal.name, NO en name directamente
            if 'matchTotal' in home_team_data and 'name' in home_team_data['matchTotal']:
                team_home_name = home_team_data['matchTotal']['name']
            elif 'name' in home_team_data:
                team_home_name = home_team_data['name']
            
            if 'matchTotal' in away_team_data and 'name' in away_team_data['matchTotal']:
                team_away_name = away_team_data['matchTotal']['name']
            elif 'name' in away_team_data:
                team_away_name = away_team_data['name']
            
            # NUEVO: Extraer goles desde matchTotal.statistics[12]
            # En IOSoccer, el índice 12 de las estadísticas corresponde a los goles
            if 'matchTotal' in home_team_data and 'statistics' in home_team_data['matchTotal']:
                home_stats = home_team_data['matchTotal']['statistics']
                if len(home_stats) > 12:
                    goals_home = home_stats[12]  # Índice 12 = goles
            
            if 'matchTotal' in away_team_data and 'statistics' in away_team_data['matchTotal']:
                away_stats = away_team_data['matchTotal']['statistics']
                if len(away_stats) > 12:
                    goals_away = away_stats[12]  # Índice 12 = goles
            
            # Fallback: buscar en campos alternativos
            if goals_home == 0 and 'goals' in home_team_data:
                goals_home = home_team_data['goals']
            if goals_away == 0 and 'goals' in away_team_data:
                goals_away = away_team_data['goals']
        
        logger.info(f"⚽ Marcador parseado: {team_home_name} {goals_home} - {goals_away} {team_away_name}")
        
        # JUGADORES ACTIVOS CORREGIDO
        active_players_count = count_real_players(players)
        
        # Determinar máximo de jugadores basado en el formato
        format_str = match_info.get('format', 8)  # Por defecto 8v8
        if isinstance(format_str, int):
            max_players_estimated = format_str * 2  # Si format es 8, entonces 8v8 = 16 jugadores
        else:
            max_players_estimated = 16  # Fallback
        
        # INFORMACIÓN DEL MAPA
        map_name = match_info.get('mapName', 'N/A')
        
        # EVENTOS Y GOLES DETALLADOS - MEJORADO
        goals_detail = parse_goals_from_events_improved(events, players, teams)
        
        logger.info(f"🎯 Información completa parseada: {active_players_count} jugadores, {len(goals_detail)} goles")
        
        # Información básica
        info = {
            'period': period_name,
            'time_display': time_display,
            'time_seconds': current_time_seconds,
            'map_name': map_name,
            'format': f"{format_str}v{format_str}" if isinstance(format_str, int) else str(format_str),
            'match_type': match_info.get('type', 'N/A'),
            'server_name': match_info.get('serverName', 'N/A'),
            
            # Información de equipos - CORREGIDA
            'team_home': team_home_name,
            'team_away': team_away_name,
            'goals_home': goals_home,
            'goals_away': goals_away,
            
            # Jugadores
            'players_count': active_players_count,
            'max_players': max_players_estimated,
            
            # Información detallada
            'players': players,
            'events': events,
            'goals_detail': goals_detail,
            
            # Para compatibilidad
            'lineup_home': extract_team_players_improved(players, 'home', team_home_name),
            'lineup_away': extract_team_players_improved(players, 'away', team_away_name),
            
            'match_stats': {
                'home_stats': teams[0].get('matchTotal', {}).get('statistics', []) if len(teams) > 0 else [],
                'away_stats': teams[1].get('matchTotal', {}).get('statistics', []) if len(teams) > 1 else []
            }
        }
        
        logger.info(f"✅ Match info parseado exitosamente: {info['team_home']} vs {info['team_away']}")
        return info
        
    except Exception as e:
        logger.error(f"❌ Error parsing match info completo: {e}")
        logger.error(f"❌ Estructura del JSON recibido: {list(match_data.keys()) if isinstance(match_data, dict) else type(match_data)}")
        return None

def count_real_players(players):
    """
    Cuenta solo los jugadores reales (no bots) - VERSIÓN MEJORADA
    """
    if not players:
        return 0
    
    real_players = 0
    for player in players:
        # Diferentes estructuras posibles
        if 'info' in player:
            player_info = player['info']
        else:
            player_info = player
        
        steam_id = player_info.get('steamId', player_info.get('steamID', ''))
        name = player_info.get('name', '')
        
        # Filtrar bots y SourceTV
        if (steam_id and 
            steam_id != 'BOT' and 
            steam_id != 'SourceTV' and 
            not name.startswith('Bot') and
            steam_id != '0'):
            real_players += 1
    
    return real_players

def extract_team_players_improved(players, team_side, team_name=None):
    """
    Extrae jugadores de un equipo específico - VERSIÓN MEJORADA
    """
    team_players = []
    
    for player in players:
        if 'info' in player:
            player_info = player['info']
            periods = player.get('matchPeriodData', [])
        else:
            player_info = player
            periods = player.get('periods', [])
        
        steam_id = player_info.get('steamId', player_info.get('steamID', ''))
        name = player_info.get('name', 'Unknown')
        
        # Filtrar bots y SourceTV
        if steam_id in ['BOT', 'SourceTV'] or not steam_id or steam_id == '0':
            continue
        
        # Buscar equipo actual del jugador en el último período
        current_team = None
        current_position = 'N/A'
        
        if periods:
            # Obtener el último período activo
            active_periods = [p for p in periods if 'info' in p and p['info']]
            if active_periods:
                last_period = active_periods[-1]
                period_info = last_period['info']
                current_team = period_info.get('team', '')
                current_position = period_info.get('position', 'N/A')
        
        # Solo incluir jugadores del equipo solicitado
        if current_team == team_side:
            team_players.append({
                'steamId': steam_id,
                'name': name,
                'position': current_position,
                'team_name': team_name or team_side.title()
            })
    
    return team_players

def parse_goals_from_events_improved(events, players, teams):
    """
    Extrae información detallada de goles desde los eventos - VERSIÓN MEJORADA
    Returns: list de dict con información de cada gol
    """
    if not events:
        return []
    
    goals = []
    
    # Crear diccionario de jugadores para búsqueda rápida
    player_dict = {}
    for player in players:
        if 'info' in player:
            player_info = player['info']
        else:
            player_info = player
        
        steam_id = player_info.get('steamId', player_info.get('steamID', ''))
        name = player_info.get('name', 'Unknown')
        
        if steam_id and steam_id != 'BOT' and steam_id != 'SourceTV':
            player_dict[steam_id] = name
    
    # Crear diccionario de nombres de equipos
    team_names = {}
    if len(teams) >= 2:
        # Equipo home (índice 0)
        if 'matchTotal' in teams[0] and 'name' in teams[0]['matchTotal']:
            team_names['home'] = teams[0]['matchTotal']['name']
        # Equipo away (índice 1)  
        if 'matchTotal' in teams[1] and 'name' in teams[1]['matchTotal']:
            team_names['away'] = teams[1]['matchTotal']['name']
    
    # Procesar eventos de goles
    for event in events:
        event_type = event.get('event', event.get('type', ''))
        
        if event_type.upper() == 'GOAL':
            scorer_id = event.get('player1SteamId', event.get('scorerSteamId', ''))
            assist_id = event.get('player2SteamId', event.get('assistSteamId', ''))
            
            # Tiempo del gol
            goal_time_seconds = event.get('second', event.get('time', 0))
            
            # Determinar equipo del gol basado en el scorer
            goal_team = event.get('team', 'unknown')
            team_name = team_names.get(goal_team, goal_team.title() if goal_team != 'unknown' else 'Unknown')
            
            goal_info = {
                'minute': seconds_to_minutes(goal_time_seconds),
                'period': event.get('period', 'N/A'),
                'team': goal_team,
                'team_name': team_name,
                'scorer_id': scorer_id,
                'assist_id': assist_id,
                'scorer_name': player_dict.get(scorer_id, 'Unknown'),
                'assist_name': player_dict.get(assist_id, '') if assist_id else '',
                'body_part': event.get('bodyPart', 1),  # 1=pie, 4=cabeza
                'position': event.get('startPosition', event.get('position', {}))
            }
            goals.append(goal_info)
    
    return goals

def seconds_to_minutes(seconds):
    """
    Convierte segundos a formato MM:SS
    """
    if not seconds:
        return "0:00"
    
    minutes = int(seconds // 60)
    remaining_seconds = int(seconds % 60)
    return f"{minutes}:{remaining_seconds:02d}"

def get_player_goals_stats(players, team_side):
    """
    Obtiene estadísticas de goles por jugador de un equipo específico
    """
    player_goals = {}
    
    for player in players:
        if 'info' in player:
            player_info = player['info']
            periods = player.get('matchPeriodData', [])
        else:
            player_info = player
            periods = player.get('periods', [])
        
        steam_id = player_info.get('steamId', player_info.get('steamID', ''))
        name = player_info.get('name', 'Unknown')
        
        if steam_id == 'BOT' or not steam_id:
            continue
        
        total_goals = 0
        total_assists = 0
        
        # Sumar goles de todos los períodos
        for period_data in periods:
            if 'info' in period_data:
                period_info = period_data['info']
                stats = period_data.get('statistics', [])
            else:
                period_info = period_data
                stats = period_data.get('stats', [])
            
            if period_info.get('team') == team_side:
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

def create_match_embed_improved(server_info):
    """Crea embed detallado con información del partido - VERSIÓN MEJORADA CON NOMBRES REALES"""
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
    period = match_info['period'].upper()
    if period in ['FIRST HALF', 'FIRST_HALF', '1ST HALF', 'PLAYING']:
        color = 0x00ff00  # Verde - Primer tiempo
    elif period in ['SECOND HALF', 'SECOND_HALF', '2ND HALF']:
        color = 0x00aa00  # Verde más oscuro - Segundo tiempo
    elif period in ['HALF TIME', 'HALF_TIME', 'HALFTIME']:
        color = 0xffa500  # Naranja - Descanso
    elif period in ['FULL TIME', 'FULL_TIME', 'FINISHED', 'END']:
        color = 0x888888  # Gris - Partido terminado
    else:
        color = 0x0099ff  # Azul - Otro estado
    
    # TÍTULO MEJORADO con nombres reales de equipos
    embed = discord.Embed(
        title=f"⚽ {server_info.name} - {match_info['format']}",
        description=f"**{match_info['team_home']}** vs **{match_info['team_away']}**",
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
    
    # MARCADOR PRINCIPAL con nombres reales de equipos
    score_text = f"**{match_info['team_home']} {match_info['goals_home']} - {match_info['goals_away']} {match_info['team_away']}**"
    
    # Emoji según el período
    period_emoji = "⚽" if period in ['FIRST HALF', 'SECOND HALF', 'PLAYING'] else "⏸️" if period == 'HALF TIME' else "🏁" if period in ['FULL TIME', 'FINISHED'] else "📅"
    
    embed.add_field(
        name="🏆 Marcador",
        value=f"{score_text}\n"
              f"⏱️ **{match_info['time_display']}** | {period_emoji} **{match_info['period']}**",
        inline=False
    )
    
    # Goles detallados por equipo con nombres reales
    if match_info.get('goals_detail'):
        home_goals = [goal for goal in match_info['goals_detail'] if goal['team'] == 'home']
        away_goals = [goal for goal in match_info['goals_detail'] if goal['team'] == 'away']
        
        # Goles equipo local
        if home_goals:
            home_goals_text = ""
            for goal in home_goals:
                assist_text = f" ({goal['assist_name']})" if goal['assist_name'] else ""
                home_goals_text += f"⚽ **{goal['minute']}** {goal['scorer_name']}{assist_text}\n"
        else:
            home_goals_text = "Sin goles"
        
        embed.add_field(
            name=f"🥅 Goles {match_info['team_home']}",
            value=home_goals_text.strip(),
            inline=True
        )
        
        # Goles equipo visitante
        if away_goals:
            away_goals_text = ""
            for goal in away_goals:
                assist_text = f" ({goal['assist_name']})" if goal['assist_name'] else ""
                away_goals_text += f"⚽ **{goal['minute']}** {goal['scorer_name']}{assist_text}\n"
        else:
            away_goals_text = "Sin goles"
        
        embed.add_field(
            name=f"🥅 Goles {match_info['team_away']}",
            value=away_goals_text.strip(),
            inline=True
        )
        
        # Campo vacío para hacer nueva línea
        embed.add_field(name="\u200b", value="\u200b", inline=True)
        
        # Máximos goleadores
        if home_goals or away_goals:
            scorer_count = {}
            for goal in match_info['goals_detail']:
                scorer = goal['scorer_name']
                scorer_count[scorer] = scorer_count.get(scorer, 0) + 1
            
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
            name=f"🥅 {match_info['team_home']}",
            value=f"**{match_info['goals_home']} goles**",
            inline=True
        )
        
        embed.add_field(
            name=f"🥅 {match_info['team_away']}",
            value=f"**{match_info['goals_away']} goles**", 
            inline=True
        )
    
    # Footer con información actualizada
    embed.set_footer(text=f"🔄 Actualizado | {datetime.now().strftime('%H:%M:%S')}")
    
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
                server_info.match_info['period'].upper() in ['FIRST HALF', 'SECOND HALF', 'PLAYING']):
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
    """Obtiene información completa del servidor con manejo robusto de errores - VERSIÓN CORREGIDA"""
    
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
            logger.warning(f"❌ A2S_INFO falló para {server['name']}")
            return ServerInfo(
                name=server['name'],
                status="🔴 Offline"
            )
        
        logger.info(f"✅ A2S_INFO exitoso para {server['name']}: {a2s_info['players']}/{a2s_info['max_players']}")
        
        # 2. Información del partido con método seguro
        match_result = await RCONManager.get_match_info_json_safe(server, RCON_PASSWORD)
        
        match_info = None
        connection_details = match_result.get('connection_info', {})
        
        if match_result['success'] and match_result['data']:
            logger.info(f"📊 JSON obtenido para {server['name']}: {len(str(match_result['data']))} caracteres")
            
            # Debug: mostrar estructura del JSON
            json_keys = list(match_result['data'].keys()) if isinstance(match_result['data'], dict) else []
            logger.info(f"🔍 Campos JSON principales: {json_keys}")
            
            # Verificar si contiene datos de partido real
            if ('matchData' in match_result['data'] or 
                'teams' in match_result['data'] or 
                'matchInfo' in match_result['data']):
                
                match_info = parse_match_info(match_result['data'])
                
                if match_info:
                    logger.info(f"✅ Match info completa parseada para {server['name']}: {match_info['team_home']} {match_info['goals_home']}-{match_info['goals_away']} {match_info['team_away']} ({match_info['time_display']})")
                else:
                    logger.warning(f"⚠️ Match info no pudo ser parseada para {server['name']}")
            else:
                logger.info(f"📄 JSON básico para {server['name']}, creando datos por defecto")
                # Crear información básica por defecto
                match_info = {
                    'period': 'Lobby/Warmup',
                    'time_display': '0:00',
                    'time_seconds': 0,
                    'map_name': a2s_info.get('map_name', 'N/A'),
                    'format': '8v8',
                    'players_count': a2s_info.get('players', 0),
                    'max_players': a2s_info.get('max_players', 16),
                    'team_home': 'Local',
                    'team_away': 'Visitante', 
                    'goals_home': 0,
                    'goals_away': 0,
                    'goals_detail': [],
                    'lineup_home': [],
                    'lineup_away': [],
                    'server_name': server['name']
                }
        else:
            logger.warning(f"⚠️ No se pudo obtener JSON para {server['name']}: {match_result['error']}")
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
    logger.info(f"🎮 IOSoccer Bot INICIADO - VERSIÓN CORREGIDA")
    logger.info(f"📊 Resumen de conectividad: {total_working}/{total_ports} puertos RCON funcionales")
    logger.info(f"🔧 Usando rcon-client con Match Info JSON mejorado")
    logger.info(f"🛡️ Modo seguro: Solo puertos específicos por servidor")
    logger.info(f"🎯 Parsing mejorado para tiempo real y marcadores")
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
                try:
                    # Intentar parsear el JSON para verificar calidad
                    json_response = cmd_test['response']
                    json_start = json_response.find('{')
                    json_end = json_response.rfind('}')
                    
                    if json_start != -1 and json_end != -1:
                        json_text = json_response[json_start:json_end+1]
                        parsed_data = json.loads(json_text)
                        
                        # Verificar si contiene datos de partido
                        has_match_data = ('matchData' in parsed_data or 'teams' in parsed_data)
                        status += f"\n📊 sv_matchinfojson: ✅ ({len(json_text)} chars, {'Match Data' if has_match_data else 'Basic Data'})"
                    else:
                        status += f"\n📊 sv_matchinfojson: ⚠️ Sin JSON válido"
                        
                except json.JSONDecodeError:
                    status += f"\n📊 sv_matchinfojson: ⚠️ JSON inválido"
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
    """Estado de todos los servidores con información detallada de partidos - VERSIÓN CORREGIDA"""
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
        
        # Log del resultado para debugging
        if server_info.match_info:
            logger.info(f"📊 {server['name']}: {server_info.match_info['team_home']} {server_info.match_info['goals_home']}-{server_info.match_info['goals_away']} {server_info.match_info['team_away']} ({server_info.match_info['time_display']}, {server_info.match_info['period']})")
        else:
            logger.info(f"📊 {server['name']}: Sin match info, {server_info.players}/{server_info.max_players} jugadores")
        
        # Limpiar field para próxima iteración
        loading_embed.clear_fields()
    
    # Mostrar resumen general primero
    status_embed = create_status_embed(servers_info)
    await message.edit(embed=status_embed)
    
    # Luego mostrar cada servidor individualmente con detalles
    for server_info in servers_info:
        match_embed = create_match_embed_improved(server_info)
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
    
    server_info = await get_server_info_robust(server)
    match_embed = create_match_embed_improved(server_info)
    
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
    """Obtiene el JSON completo del partido con análisis detallado"""
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
    
    # Analizar JSON
    json_data = match_result['data']
    json_text = json.dumps(json_data, indent=2, ensure_ascii=False)
    
    embed = discord.Embed(
        title=f"📋 Match JSON - {server['name']}",
        description=f"Puerto RCON: {match_result['working_port']}",
        color=0x00ff00
    )
    
    # Análisis de la estructura
    if isinstance(json_data, dict):
        analysis = f"**Campos principales:** {len(json_data)}\n"
        analysis += f"**Claves:** {', '.join(list(json_data.keys())[:5])}\n"
        
        if 'matchData' in json_data:
            match_core = json_data['matchData']
            analysis += f"**matchData:** ✅ ({len(match_core)} subcampos)\n"
            
            if 'teams' in match_core:
                analysis += f"**teams:** {len(match_core['teams'])} equipos\n"
            if 'players' in match_core:
                analysis += f"**players:** {len(match_core['players'])} jugadores\n"
            if 'matchEvents' in match_core:
                analysis += f"**matchEvents:** {len(match_core['matchEvents'])} eventos\n"
        
        embed.add_field(
            name="🔍 Análisis JSON",
            value=analysis,
            inline=False
        )
    
    # Mostrar JSON (truncado si es muy largo)
    if len(json_text) > 1800:
        json_preview = json_text[:1800] + "\n... (truncado para Discord)"
        embed.add_field(
            name=f"📄 JSON Data ({len(json_text)} caracteres total)",
            value=f"```json\n{json_preview}\n```",
            inline=False
        )
    else:
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

@bot.command(name='debug_parse')
async def debug_parse(ctx, server_num: int = 1):
    """Debug del parsing de match info - COMANDO NUEVO PARA DEBUGGING"""
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ Solo administradores")
        return
    
    if server_num < 1 or server_num > len(SERVERS):
        await ctx.send(f"❌ Servidor inválido. Usa 1-{len(SERVERS)}")
        return
    
    server = SERVERS[server_num - 1]
    
    loading_embed = discord.Embed(
        title=f"🔍 Debug Parsing - {server['name']}",
        description="Analizando paso a paso el parsing del JSON...",
        color=0xff6600
    )
    message = await ctx.send(embed=loading_embed)
    
    # Obtener JSON crudo
    match_result = await RCONManager.get_match_info_json_safe(server, RCON_PASSWORD)
    
    if not match_result['success']:
        embed = discord.Embed(
            title="❌ Error en Debug",
            description=f"No se pudo obtener JSON: {match_result['error']}",
            color=0xff0000
        )
        await message.edit(embed=embed)
        return
    
    json_data = match_result['data']
    
    embed = discord.Embed(
        title=f"🔍 Debug Parsing - {server['name']}",
        color=0x00aaff
    )
    
    # Paso 1: Estructura JSON
    if isinstance(json_data, dict):
        structure_info = f"**Tipo:** dict con {len(json_data)} campos\n"
        structure_info += f"**Campos raíz:** {', '.join(list(json_data.keys())[:10])}\n"
        
        if 'matchData' in json_data:
            match_core = json_data['matchData']
            structure_info += f"**matchData:** ✅ dict con {len(match_core)} campos\n"
            structure_info += f"**matchData campos:** {', '.join(list(match_core.keys())[:8])}\n"
        else:
            structure_info += f"**matchData:** ❌ No encontrado\n"
            match_core = json_data
        
        embed.add_field(
            name="📋 1. Estructura JSON",
            value=structure_info,
            inline=False
        )
        
        # Paso 2: matchInfo
        match_info = match_core.get('matchInfo', {})
        if match_info:
            info_details = f"**Campos matchInfo:** {len(match_info)}\n"
            
            # Tiempo
            time_fields = []
            for field in ['currentTime', 'matchTime', 'gameTime', 'startTime', 'endTime']:
                if field in match_info:
                    time_fields.append(f"{field}={match_info[field]}")
            
            info_details += f"**Tiempo:** {', '.join(time_fields) if time_fields else 'No encontrado'}\n"
            
            # Período
            period_fields = []
            for field in ['period', 'currentPeriod', 'lastPeriodName']:
                if field in match_info:
                    period_fields.append(f"{field}='{match_info[field]}'")
            
            info_details += f"**Período:** {', '.join(period_fields) if period_fields else 'No encontrado'}\n"
            
            # Mapa
            map_name = match_info.get('mapName', match_info.get('map', 'N/A'))
            info_details += f"**Mapa:** {map_name}\n"
            
        else:
            info_details = "❌ matchInfo no encontrado"
        
        embed.add_field(
            name="⏰ 2. Información del Partido",
            value=info_details,
            inline=False
        )
        
        # Paso 3: Equipos
        teams = match_core.get('teams', [])
        if teams:
            teams_info = f"**Cantidad equipos:** {len(teams)}\n"
            
            for i, team in enumerate(teams[:2]):
                team_name = team.get('name', team.get('teamName', f'Equipo {i+1}'))
                
                # Buscar goles en diferentes campos
                goals = 0
                if 'goals' in team:
                    goals = team['goals']
                elif 'score' in team:
                    goals = team['score']
                elif 'matchTotal' in team:
                    stats = team.get('matchTotal', {}).get('statistics', [])
                    if len(stats) > 12:
                        goals = stats[12]
                
                teams_info += f"**{team_name}:** {goals} goles\n"
        else:
            teams_info = "❌ teams no encontrado"
        
        embed.add_field(
            name="⚽ 3. Equipos y Marcador",
            value=teams_info,
            inline=False
        )
        
        # Paso 4: Jugadores
        players = match_core.get('players', [])
        if players:
            real_players = count_real_players(players)
            players_info = f"**Total jugadores:** {len(players)}\n"
            players_info += f"**Jugadores reales:** {real_players}\n"
            
            # Mostrar algunos ejemplos
            for i, player in enumerate(players[:3]):
                if 'info' in player:
                    player_info = player['info']
                else:
                    player_info = player
                
                name = player_info.get('name', 'Unknown')
                steam_id = player_info.get('steamId', 'N/A')
                players_info += f"**{name}:** {steam_id}\n"
        else:
            players_info = "❌ players no encontrado"
        
        embed.add_field(
            name="👥 4. Jugadores",
            value=players_info,
            inline=False
        )
        
        # Paso 5: Resultado del parsing
        parsed_info = parse_match_info(json_data)
        if parsed_info:
            parse_result = f"✅ **Parsing exitoso**\n"
            parse_result += f"**Marcador:** {parsed_info['team_home']} {parsed_info['goals_home']}-{parsed_info['goals_away']} {parsed_info['team_away']}\n"
            parse_result += f"**Tiempo:** {parsed_info['time_display']} ({parsed_info['period']})\n"
            parse_result += f"**Jugadores:** {parsed_info['players_count']}/{parsed_info['max_players']}\n"
        else:
            parse_result = "❌ **Parsing falló**"
        
        embed.add_field(
            name="🎯 5. Resultado Final",
            value=parse_result,
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
        title="🤖 Bot IOSoccer con Match Info JSON - VERSIÓN CORREGIDA",
        description="Bot mejorado con información detallada de partidos y debugging avanzado",
        color=0x0099ff
    )
    
    commands_help = [
        ("🎮 !status", "Estado completo de todos los servidores con info de partidos"),
        ("⚽ !server [1-2]", "Información detallada de un servidor específico"),
        ("📋 !matchjson [1-2]", "JSON completo del partido con análisis"),
        ("🔍 !debug_parse [1-2]", "(Admin) Debug paso a paso del parsing"),
        ("🔧 !rcon [1-2] [comando]", "(Admin) Ejecuta comando RCON específico"),
        ("🧪 !test_all_commands [1-2]", "(Admin) Prueba todos los comandos IOSoccer"),
        ("🔍 !diagnose", "(Admin) Diagnóstico completo del sistema"),
        ("🛠️ !fix_guide", "Guía para configurar RCON correctamente"),
        ("🏓 !ping", "Latencia del bot"),
    ]
    
    for name, description in commands_help:
        embed.add_field(name=name, value=description, inline=False)
    
    embed.add_field(
        name="📊 Información Mostrada",
        value="• Marcador en tiempo real ✅\n• Tiempo de juego preciso ✅\n• Equipos y jugadores ✅\n• Estado del partido ✅\n• Goles detallados ✅\n• Información de conexión ✅",
        inline=False
    )
    
    embed.add_field(
        name="🆕 Mejoras en esta versión",
        value="• Parsing mejorado del JSON\n• Manejo robusto de diferentes estructuras\n• Debug tools avanzadas\n• Tiempo real corregido\n• Mejor detección de equipos y goles",
        inline=False
    )
    
    embed.set_footer(text="🔧 Versión corregida con parsing mejorado y debugging")
    
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
    print("🚀 Iniciando Bot IOSoccer con Match Info JSON - VERSIÓN CORREGIDA")
    print("🔧 Parsing mejorado para tiempo real y marcadores")
    print("📡 Manejo robusto de diferentes estructuras JSON")
    print("⚽ Mostrando marcadores, tiempos, equipos y jugadores correctamente")
    print("🔍 Herramientas de debugging avanzadas incluidas")
    print("="*60)
    
    try:
        bot.run(TOKEN)
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        input("Presiona Enter para cerrar...")
