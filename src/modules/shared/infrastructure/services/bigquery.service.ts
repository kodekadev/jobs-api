import { Injectable } from '@nestjs/common';
import { BigQuery } from '@google-cloud/bigquery';
import env from '../environment';

@Injectable()
export class BigQueryService {
  private readonly bq: BigQuery;

  constructor() {
    const opts: any = { projectId: env.gcpProjectId };
    if (process.env.GCP_KEY_JSON) {
      opts.credentials = JSON.parse(process.env.GCP_KEY_JSON);
    }
    this.bq = new BigQuery(opts);
  }

  async query<T = any>(
    sql: string,
    params?: Record<string, any>,
    types?: Record<string, any>,
  ): Promise<T[]> {
    // `types` es obligatorio para parámetros que pueden venir en null:
    // BigQuery no puede inferir el tipo de un valor nulo.
    const [rows] = await this.bq.query({ query: sql, params, types });
    return rows as T[];
  }

  async dml(sql: string, params?: Record<string, any>): Promise<number> {
    const [, result] = await this.bq.query({ query: sql, params }) as any;
    return parseInt(result?.numDmlAffectedRows ?? '0', 10);
  }

  t(tableName: string): string {
    return `\`${env.gcpProjectId}.${env.bigqueryDataset}.${tableName}\``;
  }
}
