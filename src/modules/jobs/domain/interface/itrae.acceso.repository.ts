export abstract class ITraeAccesoRepository {
  abstract traeAcceso(usuario: string, pass: string, valor: number): Promise<void>;
}