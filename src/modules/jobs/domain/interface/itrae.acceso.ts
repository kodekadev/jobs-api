export abstract class ITraeAcceso {
  abstract obtenerAcceso(usuario: string, pass:string ,id: string): Promise<string>;
}