# ES-002: Guía de Desarrollo y Flujo de Trabajo
**Proyecto:** CAS Project (Credit System)

## 1. Configuración del Entorno Local
1. Clonar el repositorio.
2. Crear un entorno virtual: `python -m venv .venv`
3. Activar el entorno e instalar dependencias: `pip install -r requirements.txt`
4. Generar los archivos Python de gRPC a partir de los `.proto`:
   ```bash
   python -m grpc_tools.protoc -I./protos --python_out=./cas_server --grpc_python_out=./cas_server protos/*.proto