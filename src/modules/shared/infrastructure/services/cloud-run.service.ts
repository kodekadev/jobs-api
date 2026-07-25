import { Injectable } from '@nestjs/common';
import { GoogleAuth } from 'google-auth-library';
import * as https from 'https';

const PROJECT_ID = process.env.GCP_PROJECT_ID || 'jobs-425301';
const REGION = 'us-central1';
const JOB_NAME = 'auto-cv-update';

@Injectable()
export class CloudRunService {
  private auth: GoogleAuth;

  constructor() {
    const opts: any = {
      scopes: ['https://www.googleapis.com/auth/cloud-platform'],
    };
    if (process.env.GCP_KEY_JSON) {
      opts.credentials = JSON.parse(process.env.GCP_KEY_JSON);
    }
    this.auth = new GoogleAuth(opts);
  }

  async triggerRegisterJob(userId: string): Promise<void> {
    try {
      const token = await this.auth.getAccessToken();
      // Solo pasa SINGLE_USER_ID — el job ya tiene command/args correctos (main.py --update-cv)
      const body = JSON.stringify({
        overrides: {
          containerOverrides: [{
            env: [{ name: 'SINGLE_USER_ID', value: userId }],
          }],
        },
      });

      await new Promise<void>((resolve, reject) => {
        const req = https.request({
          hostname: 'run.googleapis.com',
          path: `/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB_NAME}:run`,
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${token}`,
            'Content-Type': 'application/json',
            'Content-Length': Buffer.byteLength(body),
          },
        }, (res) => {
          let d = '';
          res.on('data', (c) => d += c);
          res.on('end', () => {
            if (res.statusCode && res.statusCode >= 400) {
              console.error(`[CloudRun] register job error ${res.statusCode}: ${d.slice(0, 300)}`);
            } else {
              console.log(`[CloudRun] register job triggered for ${userId}`);
            }
            resolve();
          });
        });
        req.on('error', (e) => {
          console.error('[CloudRun] request error:', e.message);
          resolve();
        });
        req.write(body);
        req.end();
      });
    } catch (e: any) {
      console.error('[CloudRun] triggerRegisterJob failed:', e.message);
    }
  }
}
