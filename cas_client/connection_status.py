"""Estado de la conexión con el servidor, observado en vivo.

Motivo: hasta ahora el operador no tenía forma de saber que el enlace con el
PC servidor se había caído hasta que apretaba un botón y la acción fallaba. En
la LAN real esa caída ocurre sola -- ver el bloque de keepalive en
cas_client/config.py -- así que el cliente puede quedarse minutos "aparentando"
estar conectado. Esto expone el hecho en la barra superior.

No hace ningún sondeo propio: se cuelga de la máquina de estados de
conectividad que el canal gRPC ya mantiene (`Channel.subscribe`), que es la
misma que los PING de keepalive alimentan. Un sondeo con temporizador sería
tráfico redundante y, peor, podría contradecir al canal.

`Channel.subscribe` llama de vuelta desde un hilo interno de gRPC, así que el
estado se reemite como señal de Qt: este objeto vive en el hilo principal, de
modo que Qt entrega la señal en cola y el slot corre en el hilo de la UI,
donde tocar widgets es seguro. Mismo patrón que `session.session_events`.
"""

import grpc
from PySide6.QtCore import QObject, Signal

# Estados del canal que cuentan como "hay línea con el servidor". IDLE entra a
# propósito: un canal recién creado, o uno que se durmió por falta de uso,
# está sano -- se conecta en la próxima llamada. Tratarlo como caído mostraría
# una alarma falsa cada vez que la app queda un rato quieta.
_ESTADOS_CONECTADOS = frozenset(
    {
        grpc.ChannelConnectivity.IDLE,
        grpc.ChannelConnectivity.READY,
        grpc.ChannelConnectivity.CONNECTING,
    }
)


class ConnectionMonitor(QObject):
    """Observa un canal gRPC y emite `changed(bool)` cuando cambia si hay o no
    conexión.

    Se observa un solo canal (el de AuthService) y no los cinco: todos apuntan
    al mismo host:puerto y comparten la suerte de la red, así que cinco
    suscripciones darían cinco veces la misma noticia. El canal de Auth es el
    indicado porque es el único que existe antes del login.
    """

    changed = Signal(bool)

    def __init__(self, channel: grpc.Channel, parent: QObject | None = None):
        super().__init__(parent)
        self._channel = channel
        # None y no True/False: el primer callback siempre informa un estado,
        # y arrancar en None garantiza que ese primero se propague en vez de
        # perderse por coincidir con el valor inicial supuesto.
        self._connected: bool | None = None
        # try_to_connect=False: suscribirse no debe forzar una conexión. El
        # monitor observa, no provoca -- si abriera el canal por su cuenta,
        # estaría reportando el resultado de su propia acción.
        self._channel.subscribe(self._on_state, try_to_connect=False)

    def _on_state(self, state: grpc.ChannelConnectivity) -> None:
        conectado = state in _ESTADOS_CONECTADOS
        if conectado == self._connected:
            return
        self._connected = conectado
        self.changed.emit(conectado)

    def stop(self) -> None:
        """Deja de observar. Sin esto gRPC conserva la referencia al callback
        y seguiría invocándolo durante el cierre de la app, cuando los widgets
        que escuchan ya pueden estar destruidos."""
        try:
            self._channel.unsubscribe(self._on_state)
        except Exception:  # noqa: BLE001 -- canal ya cerrado durante el apagado
            pass
