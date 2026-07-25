import { Injectable } from '@nestjs/common';

@Injectable()
export class TelegramService {
  private readonly token = process.env.TELEGRAM_BOT_TOKEN || '';
  private readonly chatId = process.env.TELEGRAM_CHAT_ID || '';

  async send(message: string): Promise<void> {
    if (!this.token || !this.chatId) return;
    const url = `https://api.telegram.org/bot${this.token}/sendMessage`;
    await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: this.chatId, text: message, parse_mode: 'HTML' }),
    }).catch(() => null);
  }

  newUser(nombre: string, email: string, via: 'email' | 'google'): void {
    const icon = via === 'google' ? '🔵' : '📧';
    this.send(
      `${icon} <b>Nuevo usuario</b>\n` +
      `👤 ${nombre}\n` +
      `✉️ ${email}\n` +
      `🔗 Vía: ${via === 'google' ? 'Google' : 'Email'}`
    ).catch(() => null);
  }
}
