import * as dotenv from 'dotenv';
dotenv.config();

const env = {
  port: parseInt(process.env.PORT || '8080'),
  gcpProjectId: process.env.GCP_PROJECT_ID || 'jobs-425301',
  bigqueryDataset: process.env.BIGQUERY_DATASET || 'DWH',
  jwtSecret: process.env.JWT_SECRET || 'jobs_secret_key',
  resendApiKey: process.env.RESEND_API_KEY || '',
  flowApiKey: process.env.FLOW_API_KEY || '',
  flowSecretKey: process.env.FLOW_SECRET_KEY || '',
  flowBaseUrl: process.env.FLOW_BASE_URL || 'https://www.flow.cl/api',
  frontendUrl: process.env.FRONTEND_URL || 'http://localhost:3000',
  backendUrl: process.env.BACKEND_URL || 'http://localhost:8080',
  autoJobName: process.env.AUTO_JOB_NAME || 'auto-postulaciones',
  gcpRegion: process.env.GCP_REGION || 'us-central1',
  jenkinsUrl: process.env.JENKINS_URL || 'http://localhost:8080',
  jenkinsJob: process.env.JENKINS_JOB || 'crear-cuenta-portal',
  jenkinsUser: process.env.JENKINS_USER || '',
  jenkinsToken: process.env.JENKINS_TOKEN || '',
  fromEmail: process.env.FROM_EMAIL || 'AplicAI <postulaciones@aplicai.cl>',
  gcsBucketImages: process.env.GCS_BUCKET_IMAGES || 'jobs-profile-images',
  gcsBucketCv: process.env.GCS_BUCKET_CV || 'jobs-profile-cv',
  anthropicApiKey: process.env.ANTHROPIC_API_KEY || '',
  adminSecret: process.env.ADMIN_SECRET || '',
};

export default env;
