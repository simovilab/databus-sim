# dashboard.py
import matplotlib.pyplot as plt
import pandas as pd

plt.style.use("default")

# === 1️⃣ OTP (On-Time Performance) ===
def plot_otp(df):
    df_stop = df[df["type"] == "stop"].copy()
    if df_stop.empty or "on_time" not in df_stop.columns:
        print("⚠️ No hay datos de OTP registrados.")
        return

    df_stop["on_time"] = df_stop["on_time"].astype(float)
    otp_rate = df_stop["on_time"].mean() * 100

    plt.figure()
    df_stop["on_time"].rolling(20, min_periods=1).mean().plot()
    plt.title(f"On-Time Performance (OTP)\nCumplimiento promedio: {otp_rate:.1f}%")
    plt.xlabel("Índice de registro")
    plt.ylabel("Puntualidad (1 = sí, 0 = no)")
    plt.grid(True)
    plt.show()

    print(f"✅ OTP promedio: {otp_rate:.2f}%")
    if "otp_diff_sec" in df_stop.columns:
        print(f"⏱️ Desviación promedio respecto al horario: {df_stop['otp_diff_sec'].mean():.2f} s")


# === 2️⃣ Headway adherence ===
def plot_headway_adherence(df):
    df_stop = df[df["type"] == "stop"].copy()
    if df_stop.empty or "tick" not in df_stop.columns:
        print("⚠️ No hay datos de headway registrados.")
        return

    df_stop = df_stop.sort_values(["vehicle_id", "tick"])
    df_stop["headway_ticks"] = df_stop.groupby("vehicle_id")["tick"].diff().fillna(0)

    plt.figure()
    df_stop["headway_ticks"].rolling(10, min_periods=1).mean().plot()
    plt.title("Headway adherence — Variación del intervalo entre llegadas")
    plt.xlabel("Índice de registro")
    plt.ylabel("Headway (ticks ≈ segundos)")
    plt.grid(True)
    plt.show()

    print(f"⏱️ Headway promedio: {df_stop['headway_ticks'].mean():.2f} ticks")


# === 3️⃣ Gaps (distancia entre vehículos) ===
def plot_gaps(df):
    df_g = df[df["type"] == "vehicle"].copy()
    if df_g.empty or "gap_m" not in df_g.columns:
        print("⚠️ No hay datos de gaps registrados.")
        return

    plt.figure()
    df_g.groupby("tick")["gap_m"].mean().plot()
    plt.title("Distancia promedio entre vehículos (Gap)")
    plt.xlabel("Tick")
    plt.ylabel("Distancia promedio (m)")
    plt.grid(True)
    plt.show()

    print(f"🚍 Distancia promedio entre vehículos: {df_g['gap_m'].mean():.2f} m")


# === 4️⃣ System overview ===
def plot_system_overview(df):
    df_sys = df[df["type"] == "system"].copy()
    if df_sys.empty:
        print("⚠️ No hay métricas del sistema registradas.")
        return

    fig, ax1 = plt.subplots()
    ax2 = ax1.twinx()

    ax1.plot(df_sys["tick"], df_sys["active_vehicles"], color="tab:blue", label="Vehículos activos")
    ax2.plot(df_sys["tick"], df_sys["avg_tick_time_ms"], color="tab:orange", label="Tiempo promedio/tick")

    ax1.set_xlabel("Tick")
    ax1.set_ylabel("Vehículos activos", color="tab:blue")
    ax2.set_ylabel("Tiempo promedio por tick (ms)", color="tab:orange")
    plt.title("Evolución del sistema")
    fig.tight_layout()
    plt.grid(True)
    plt.show()


# === Dashboard principal ===
def dashboard(df):
    print("📊 Generando Dashboard con métricas reales (OTP, Headway, Gaps)...\n")

    plot_otp(df)
    plot_headway_adherence(df)
    plot_gaps(df)
    plot_system_overview(df)

    print("\n✅ Dashboard generado con éxito.")




