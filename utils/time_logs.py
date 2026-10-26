# utils/time_logs.py
import datetime
import re

def log(msg):
    """Log avec timestamp + écriture dans logs.txt"""
    now = datetime.datetime.now().strftime("%H:%M:%S")
    
    # Affichage console avec couleur bleue pour le timestamp
    print(f"\033[34m[{now}]\033[0m {msg}")
    
    # Écriture dans logs.txt (sans couleurs ANSI)
    try:
        from aitb.logger import append_to_logs_txt
        
        # Supprime les codes couleur ANSI du message
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        clean_message = ansi_escape.sub('', msg)
        
        # Écrit dans logs.txt
        append_to_logs_txt(f"[{now}] {clean_message}")
    except Exception:
        pass  # Silencieux si erreur d'écriture