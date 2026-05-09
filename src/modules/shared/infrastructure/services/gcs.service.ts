import { Injectable } from '@nestjs/common';
import { Storage } from '@google-cloud/storage';
import env from '../environment';

@Injectable()
export class GcsService {
  private readonly storage: Storage;

  constructor() {
    const opts: any = { projectId: env.gcpProjectId };
    if (process.env.GCP_KEY_JSON) {
      opts.credentials = JSON.parse(process.env.GCP_KEY_JSON);
    }
    this.storage = new Storage(opts);
  }

  async uploadBuffer(
    bucketName: string,
    fileName: string,
    buffer: Buffer,
    contentType: string,
  ): Promise<string> {
    const bucket = this.storage.bucket(bucketName);
    const file = bucket.file(fileName);

    await file.save(buffer, { resumable: false, contentType });

    return `https://storage.googleapis.com/${bucketName}/${fileName}`;
  }

  async getSignedUrl(bucketName: string, fileName: string): Promise<string> {
    const bucket = this.storage.bucket(bucketName);
    const file = bucket.file(fileName);

    const [exists] = await file.exists();
    if (!exists) return '';

    const [url] = await file.getSignedUrl({
      action: 'read',
      expires: Date.now() + 15 * 60 * 1000,
    });

    return url;
  }

  extractFileName(url: string): string {
    if (!url) return '';
    return url.split('/').pop() || '';
  }
}
