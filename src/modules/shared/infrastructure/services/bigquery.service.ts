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

  async query<T = any>(sql: string, params?: Record<string, any>): Promise<T[]> {
    const [rows] = await this.bq.query({ query: sql, params });
    return rows as T[];
  }

  t(tableName: string): string {
    return `\`${env.gcpProjectId}.${env.bigqueryDataset}.${tableName}\``;
  }
}
