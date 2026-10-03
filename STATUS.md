# STATUS · Cierre trimestral wFIAT

Demo independiente para Ripio, por Máximo Sckell. No es una herramienta oficial de Ripio. Usa solo datos públicos: las blockchains y las certificaciones que Ripio publica.

Página: https://conciliacion-wfiat.vercel.app

## Qué hace
Un agente arma solo la parte onchain del cierre trimestral de las stablecoins wFIAT (wARS, wBRL, wMXN, wCOP, wCLP y wPEN). Cuenta los tokens de cada red al corte, lista cada emisión y cada quema con su transacción, concilia apertura + emisiones − quemas = cierre y avisa a Finanzas por Slack qué cambió y qué necesita una persona.

## Estado actual
- Terminado y en producción. El monitor diario corre a las 12:00 UTC y el cierre corre solo el día después de cada fin de trimestre.
- El método coincide con la cantidad de tokens certificada por el contador en 9 de las 9 certificaciones publicadas (cortes 31/03/2026 y 30/06/2026).
- Probado con el mismo método en cinco cortes reales, sin ajustes por período.

## Último cierre: 30/09/2026
- 54 de 54 pares de token y red conciliados con diferencia cero, en 9 redes con contratos de 24 revisadas.
- Arc aparece como red nueva, con su bloque de creación como evidencia.
- El agente de excepciones investigó 9 movimientos, probó 4 con evidencia y dejó 5 como tareas para una persona.
- Paquete para el contador: `data/closes/2026-09-30/paquete_cierre_2026-09-30.xlsx`.

## Cómo se corre
Los pasos completos están en el README, sección "How to run". Lo básico:

```sh
uv sync
uv run pytest
uv run cierre close --cutoff 2026-09-30
```
