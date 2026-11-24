"""Valores de referencia por cultivo (informe ES3, tablas 12 a 15: Shareef et al. 2024, Jones 2014, UF/IFAS HS1422 y los estudios
citados en cada tabla).

Cada cultivo muestra SOLO las variables que aparecen en su tabla, con el valor o rango tal como lo dice la tabla:
  * Lechuga, albahaca y frutilla: pH, EC, temperatura de la solución y del ambiente, humedad, fotoperiodo, PPFD, N, P, K y consumo de agua.
  * Tomate cherry: lo mismo, salvo la temperatura ambiente (su tabla solo da temperaturas de germinación y de plántula).
Caudal, nivel de agua, amoníaco y CO₂ no tienen una referencia verificable por especie, por eso no se muestran.

REFERENCIAS guarda los números con los que se arman las reglas de alerta: (mín, máx) = rango; un número solo = valor de referencia.
TEXTOS guarda lo que se muestra en pantalla, escrito como en el informe.
"""

REFERENCIAS = {
    "Lechuga": {"ph": (5.0, 7.0), "ec": (1.5, 2.5), "temperatura": (18, 25), "temperatura_ambiente": (18, 27), "humedad": (45, 80),
                "fotoperiodo": (14, 17), "ppfd": (150, 250), "nitrogeno": 200, "fosforo": 50, "potasio": 300},
    "Albahaca": {"ph": (5.5, 6.5), "ec": (1.1, 1.6), "temperatura": (18, 24), "temperatura_ambiente": (18, 30), "humedad": (50, 85),
                 "fotoperiodo": 16, "ppfd": (80, 250), "nitrogeno": 210, "fosforo": 80, "potasio": 275},
    "Tomate cherry": {"ph": (5.5, 6.5), "ec": (1.6, 4.2), "temperatura": (18, 27), "humedad": (50, 80),
                      "fotoperiodo": 15, "ppfd": (50, 200), "nitrogeno": 225, "fosforo": 50, "potasio": 321},
    "Frutilla": {"ph": 6.0, "ec": (1.8, 2.0), "temperatura": (18, 30), "temperatura_ambiente": (18, 30), "humedad": (40, 80),
                 "fotoperiodo": (12, 16), "ppfd": (115, 350), "nitrogeno": 207, "fosforo": 65, "potasio": 184},
}

TEXTOS = {
    "Lechuga": {"ph": "5,0–7,0", "ec": "1,5–2,5 mS/cm", "temperatura": "18–25 °C", "temperatura_ambiente": "18–27 °C", "humedad": "45–80 %",
                "fotoperiodo": "14–17 h/día", "ppfd": "150–250 µmol/m²/s", "nitrogeno": "200 mg/L", "fosforo": "50 mg/L", "potasio": "300 mg/L",
                "consumo_hidrico": "75–100 mL/planta/día"},
    "Albahaca": {"ph": "5,5–6,5", "ec": "1,1–1,6 mS/cm", "temperatura": "18–24 °C", "temperatura_ambiente": "18–30 °C", "humedad": "50–85 %",
                 "fotoperiodo": "16 h/día", "ppfd": "80–250 µmol/m²/s", "nitrogeno": "210 mg/L", "fosforo": "80 mg/L", "potasio": "275 mg/L",
                 "consumo_hidrico": "502 mL/planta/día (máximo)"},
    "Tomate cherry": {"ph": "5,5–6,5", "ec": "1,6 vegetativo · 3,2–4,2 fructificación", "temperatura": "18–27 °C", "humedad": "50–80 %",
                      "fotoperiodo": "15 h/día", "ppfd": "50–200 µmol/m²/s", "nitrogeno": "225 mg/L", "fosforo": "50 mg/L", "potasio": "321 mg/L",
                      "consumo_hidrico": "603,2–767,6 mL/planta/día"},
    "Frutilla": {"ph": "6,0", "ec": "1,8 vegetativo · 2,0 fructificación", "temperatura": "18–30 °C", "temperatura_ambiente": "18–30 °C",
                 "humedad": "40–80 %", "fotoperiodo": "12–16 h/día", "ppfd": "115–350 µmol/m²/s",
                 "nitrogeno": "207 mg/L vegetativo · 182 fructificación", "fosforo": "65 mg/L vegetativo · 82 fructificación",
                 "potasio": "184 mg/L vegetativo · 301 fructificación", "consumo_hidrico": "50,2–390 mL/planta/día (promedio 215,9)"},
}

# Variables sin referencia verificable por especie: no se muestran en ningún cultivo ni generan alertas.
SIN_REFERENCIA = frozenset({"caudal", "nivel_agua", "amoniaco", "co2", "parametro_cultivo", "nutrientes", "luz_intensidad"})


def variables_del_cultivo(nombre):
    """Variables que muestra un cultivo con tabla de referencia (las de su tabla), o None si el cultivo no tiene tabla."""
    textos = TEXTOS.get(nombre)
    return {v: True for v in textos} if textos else None


def texto(cultivo, variable):
    return (TEXTOS.get(cultivo) or {}).get(variable)
