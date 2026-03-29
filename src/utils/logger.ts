/**
 * Structured logging utility using Winston
 * Provides consistent logging across the application
 */

import winston from 'winston';

const { combine, timestamp, printf, colorize, errors } = winston.format;

// Custom log format
const logFormat = printf(({ level, message, timestamp, stack, ...meta }) => {
  let log = `${timestamp} - ${level}: ${message}`;

  // Add stack trace for errors
  if (stack) {
    log += `\n${stack}`;
  }

  // Add metadata if present
  if (Object.keys(meta).length > 0) {
    log += ` ${JSON.stringify(meta)}`;
  }

  return log;
});

// Create winston logger instance
const baseLogger = winston.createLogger({
  level: process.env.LOG_LEVEL || 'info',
  format: combine(
    timestamp({ format: 'YYYY-MM-DD HH:mm:ss' }),
    errors({ stack: true }),
    logFormat
  ),
  transports: [
    new winston.transports.Console({
      format: combine(
        colorize(),
        timestamp({ format: 'YYYY-MM-DD HH:mm:ss' }),
        errors({ stack: true }),
        logFormat
      ),
    }),
  ],
});

// Add file transport for production
if (process.env.NODE_ENV === 'production') {
  baseLogger.add(
    new winston.transports.File({
      filename: 'logs/error.log',
      level: 'error',
    })
  );
  baseLogger.add(
    new winston.transports.File({
      filename: 'logs/combined.log',
    })
  );
}

/**
 * Logger class with component-specific context
 */
export class Logger {
  constructor(private component: string) {}

  /**
   * Create a logger for a specific component
   */
  static create(component: string): Logger {
    return new Logger(component);
  }

  /**
   * Log debug message
   */
  debug(message: string, meta?: any): void {
    baseLogger.debug(message, { component: this.component, ...meta });
  }

  /**
   * Log info message
   */
  info(message: string, meta?: any): void {
    baseLogger.info(message, { component: this.component, ...meta });
  }

  /**
   * Log warning message
   */
  warn(message: string, meta?: any): void {
    baseLogger.warn(message, { component: this.component, ...meta });
  }

  /**
   * Log error message
   */
  error(message: string, error?: Error | any, meta?: any): void {
    const logMeta = { component: this.component, ...meta };

    if (error instanceof Error) {
      baseLogger.error(message, { ...logMeta, error: error.message, stack: error.stack });
    } else if (error) {
      baseLogger.error(message, { ...logMeta, error });
    } else {
      baseLogger.error(message, logMeta);
    }
  }

  /**
   * Log with custom level
   */
  log(level: string, message: string, meta?: any): void {
    baseLogger.log(level, message, { component: this.component, ...meta });
  }
}

// Export default logger
export const logger = Logger.create('App');