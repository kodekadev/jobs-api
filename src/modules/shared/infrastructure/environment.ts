import * as dotenv from 'dotenv';

class Environment {
  public static getInstance(): Environment {
    return this._instance;
  }

  private static _instance: Environment = new Environment();
  public port: number;
  public env: string;
  public gcpProjectId: string;
  public googleApplicationCredentials: string;
  public bigquery_dataset: string;

  constructor() {
    dotenv.config();
    this.port = Number(process.env.PORT) || 3000;
    this.gcpProjectId = process.env.GCP_PROJECT_ID ;
    this.googleApplicationCredentials = process.env.GOOGLE_APPLICATION_CREDENTIALS || '';
    this.bigquery_dataset = process.env.BIGQUERY_DATASET || '';    
    this.env = process.env.NODE_ENV;
  }
}

const environment = Environment.getInstance();
export default environment;
